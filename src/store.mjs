import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { buildStateGraph, traceGraph } from "./graph.mjs";
import { changedPaths, diffJson, stableStringify } from "./json-diff.mjs";
import { memoryText, objectIdentity, objectSummary, sanitizeObject } from "./kubernetes-object.mjs";
import { parseDurationSeconds, subtractSeconds, toIso } from "./time.mjs";

function parseJson(value, fallback = null) {
  if (value === null || value === undefined || value === "") return fallback;
  return JSON.parse(value);
}

function cosineSimilarity(left, right) {
  if (!left?.length || left.length !== right?.length) return 0;
  let dot = 0;
  let leftNorm = 0;
  let rightNorm = 0;
  for (let index = 0; index < left.length; index += 1) {
    dot += left[index] * right[index];
    leftNorm += left[index] ** 2;
    rightNorm += right[index] ** 2;
  }
  return leftNorm && rightNorm ? dot / (Math.sqrt(leftNorm) * Math.sqrt(rightNorm)) : 0;
}

function eventRow(row) {
  return {
    id: row.id,
    source: row.source,
    observedAt: row.observed_at,
    eventAt: row.event_at,
    action: row.action,
    resourceKey: row.resource_key,
    apiVersion: row.api_version,
    kind: row.kind,
    namespace: row.namespace,
    name: row.name,
    uid: row.uid,
    resourceVersion: row.resource_version,
    patch: parseJson(row.patch_json, []),
    summary: row.summary,
    actor: row.actor,
    auditId: row.audit_id,
    memoryText: row.memory_text
  };
}

const knownResourceKinds = {
  configmaps: "ConfigMap",
  cronjobs: "CronJob",
  daemonsets: "DaemonSet",
  deployments: "Deployment",
  endpoints: "Endpoints",
  ingresses: "Ingress",
  jobs: "Job",
  namespaces: "Namespace",
  networkpolicies: "NetworkPolicy",
  persistentvolumeclaims: "PersistentVolumeClaim",
  pods: "Pod",
  replicasets: "ReplicaSet",
  secrets: "Secret",
  serviceaccounts: "ServiceAccount",
  services: "Service",
  statefulsets: "StatefulSet"
};

export class TemporalStore {
  constructor(dbPath = ":memory:") {
    if (dbPath !== ":memory:") fs.mkdirSync(path.dirname(path.resolve(dbPath)), { recursive: true });
    this.db = new DatabaseSync(dbPath);
    this.db.exec("PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000;");
    this.migrate();
  }

  migrate() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cluster_id TEXT NOT NULL,
        source TEXT NOT NULL,
        authoritative INTEGER NOT NULL DEFAULT 1,
        observed_at TEXT NOT NULL,
        event_at TEXT NOT NULL,
        action TEXT NOT NULL,
        resource_key TEXT NOT NULL,
        api_version TEXT NOT NULL,
        kind TEXT NOT NULL,
        namespace TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,
        uid TEXT NOT NULL DEFAULT '',
        resource_version TEXT NOT NULL DEFAULT '',
        object_json TEXT,
        patch_json TEXT NOT NULL,
        summary TEXT NOT NULL,
        actor TEXT NOT NULL DEFAULT '',
        audit_id TEXT NOT NULL DEFAULT '',
        memory_text TEXT NOT NULL,
        embedding_json TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_changes_time ON changes(cluster_id, event_at, id);
      CREATE INDEX IF NOT EXISTS idx_changes_resource ON changes(cluster_id, resource_key, event_at, id);
      CREATE INDEX IF NOT EXISTS idx_changes_namespace ON changes(cluster_id, namespace, event_at);

      CREATE TABLE IF NOT EXISTS audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cluster_id TEXT NOT NULL,
        audit_id TEXT NOT NULL,
        event_at TEXT NOT NULL,
        stage TEXT NOT NULL DEFAULT '',
        verb TEXT NOT NULL DEFAULT '',
        actor TEXT NOT NULL DEFAULT '',
        source_ips_json TEXT NOT NULL DEFAULT '[]',
        resource_key TEXT NOT NULL DEFAULT '',
        namespace TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '',
        response_code INTEGER,
        request_json TEXT,
        response_json TEXT,
        UNIQUE(cluster_id, audit_id, stage)
      );
      CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_events(cluster_id, event_at);
      CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_events(cluster_id, resource_key, event_at);

      CREATE TABLE IF NOT EXISTS k8s_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cluster_id TEXT NOT NULL,
        event_uid TEXT NOT NULL DEFAULT '',
        event_at TEXT NOT NULL,
        namespace TEXT NOT NULL DEFAULT '',
        regarding_uid TEXT NOT NULL DEFAULT '',
        regarding_kind TEXT NOT NULL DEFAULT '',
        regarding_name TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '',
        type TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        reporting_controller TEXT NOT NULL DEFAULT '',
        count INTEGER NOT NULL DEFAULT 1,
        UNIQUE(cluster_id, event_uid, event_at, reason, count)
      );
      CREATE INDEX IF NOT EXISTS idx_k8s_events_time ON k8s_events(cluster_id, event_at);

      CREATE TABLE IF NOT EXISTS metric_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cluster_id TEXT NOT NULL,
        metric_name TEXT NOT NULL,
        sampled_at TEXT NOT NULL,
        value REAL NOT NULL,
        labels_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(cluster_id, metric_name, sampled_at, labels_json)
      );
      CREATE INDEX IF NOT EXISTS idx_metrics_time ON metric_samples(cluster_id, sampled_at);
    `);
  }

  close() {
    this.db.close();
  }

  recomputePatch(row, previousRow) {
    if (!row) return;
    const before = previousRow?.object_json == null ? undefined : JSON.parse(previousRow.object_json);
    const after = row.object_json == null ? undefined : JSON.parse(row.object_json);
    const patch = diffJson(before, after);
    const identity = {
      key: row.resource_key,
      apiVersion: row.api_version,
      kind: row.kind,
      namespace: row.namespace,
      name: row.name,
      uid: row.uid,
      resourceVersion: row.resource_version
    };
    const summary = objectSummary(row.action, identity, patch);
    const text = memoryText({ action: row.action, identity, patch, actor: row.actor, eventAt: row.event_at });
    this.db.prepare(`
      UPDATE changes SET patch_json = ?, summary = ?,
        embedding_json = CASE WHEN memory_text = ? THEN embedding_json ELSE NULL END,
        memory_text = ?
      WHERE id = ?
    `).run(stableStringify(patch), summary, text, text, row.id);
  }

  repairPatchNeighbors(clusterId, resourceKey, insertedId) {
    const current = this.db.prepare("SELECT * FROM changes WHERE id = ?").get(insertedId);
    const previous = this.db.prepare(`
      SELECT * FROM changes WHERE cluster_id = ? AND resource_key = ?
        AND (event_at < ? OR (event_at = ? AND id < ?))
      ORDER BY event_at DESC, id DESC LIMIT 1
    `).get(clusterId, resourceKey, current.event_at, current.event_at, current.id);
    const next = this.db.prepare(`
      SELECT * FROM changes WHERE cluster_id = ? AND resource_key = ?
        AND (event_at > ? OR (event_at = ? AND id > ?))
      ORDER BY event_at ASC, id ASC LIMIT 1
    `).get(clusterId, resourceKey, current.event_at, current.event_at, current.id);
    this.recomputePatch(current, previous);
    this.recomputePatch(next, current);
  }

  recordResourceChange({ clusterId, source = "watch", action, object, observedAt, eventAt, actor = "", auditId = "", embedding = null }) {
    if (!clusterId) throw new Error("clusterId is required");
    const normalizedAction = String(action ?? "MODIFIED").toUpperCase();
    if (!object || typeof object !== "object") throw new Error("object is required");
    const sanitized = sanitizeObject(object);
    const identity = objectIdentity(sanitized);
    const observationTime = toIso(observedAt, "observedAt");
    const effectiveTime = toIso(eventAt ?? observationTime, "eventAt");

    if (identity.resourceVersion) {
      const duplicate = this.db.prepare(`
        SELECT id FROM changes
        WHERE cluster_id = ? AND source = ? AND resource_key = ? AND resource_version = ? AND action = ?
        LIMIT 1
      `).get(clusterId, source, identity.key, identity.resourceVersion, normalizedAction);
      if (duplicate) return { id: duplicate.id, duplicate: true };
    }

    const previousRow = this.db.prepare(`
      SELECT object_json FROM changes
      WHERE cluster_id = ? AND resource_key = ? AND authoritative = 1
        AND event_at <= ?
      ORDER BY event_at DESC, id DESC LIMIT 1
    `).get(clusterId, identity.key, effectiveTime);
    const previous = previousRow?.object_json == null ? undefined : JSON.parse(previousRow.object_json);
    const after = normalizedAction === "DELETED" ? undefined : sanitized;
    const patch = diffJson(previous, after);
    const summary = objectSummary(normalizedAction, identity, patch);
    const text = memoryText({ action: normalizedAction, identity, patch, actor, eventAt: effectiveTime });

    const result = this.db.prepare(`
      INSERT INTO changes (
        cluster_id, source, authoritative, observed_at, event_at, action,
        resource_key, api_version, kind, namespace, name, uid, resource_version,
        object_json, patch_json, summary, actor, audit_id, memory_text, embedding_json
      ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      clusterId, source, observationTime, effectiveTime, normalizedAction,
      identity.key, identity.apiVersion, identity.kind, identity.namespace, identity.name,
      identity.uid, identity.resourceVersion, after === undefined ? null : stableStringify(after),
      stableStringify(patch), summary, actor, auditId, text,
      embedding ? stableStringify(embedding) : null
    );
    const id = Number(result.lastInsertRowid);
    this.repairPatchNeighbors(clusterId, identity.key, id);
    const repaired = this.db.prepare("SELECT patch_json, summary FROM changes WHERE id = ?").get(id);
    return { id, duplicate: false, identity, patch: parseJson(repaired.patch_json, []), summary: repaired.summary };
  }

  recordAuditEvent(clusterId, auditEvent) {
    const objectRef = auditEvent.objectRef ?? {};
    const version = objectRef.apiVersion ?? "v1";
    const apiVersion = objectRef.apiGroup && !version.includes("/") ? `${objectRef.apiGroup}/${version}` : version;
    const resourceName = objectRef.resource ?? "Unknown";
    const kind = auditEvent.responseObject?.kind ?? auditEvent.requestObject?.kind ?? knownResourceKinds[resourceName]
      ?? resourceName.replace(/s$/, "").replace(/(^|-)([a-z])/g, (_, prefix, char) => prefix + char.toUpperCase());
    const resourceKey = objectRef.name ? `${apiVersion}|${kind}|${objectRef.namespace || "_cluster"}|${objectRef.name}` : "";
    const eventAt = toIso(auditEvent.stageTimestamp ?? auditEvent.requestReceivedTimestamp, "audit timestamp");
    const actor = auditEvent.user?.username ?? "";
    const auditId = auditEvent.auditID ?? "";
    const result = this.db.prepare(`
      INSERT OR IGNORE INTO audit_events (
        cluster_id, audit_id, event_at, stage, verb, actor, source_ips_json,
        resource_key, namespace, name, response_code, request_json, response_json
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      clusterId, auditId, eventAt, auditEvent.stage ?? "", auditEvent.verb ?? "", actor,
      stableStringify(auditEvent.sourceIPs ?? []), resourceKey, objectRef.namespace ?? "", objectRef.name ?? "",
      auditEvent.responseStatus?.code ?? null,
      auditEvent.requestObject ? stableStringify(sanitizeObject(auditEvent.requestObject)) : null,
      auditEvent.responseObject ? stableStringify(sanitizeObject(auditEvent.responseObject)) : null
    );
    return { inserted: result.changes > 0, auditId, resourceKey };
  }

  recordKubernetesEvent(clusterId, input) {
    const metadata = input.metadata ?? {};
    const regarding = input.regarding ?? input.involvedObject ?? {};
    const eventAt = toIso(input.eventTime ?? input.series?.lastObservedTime ?? input.deprecatedLastTimestamp ?? input.lastTimestamp ?? metadata.creationTimestamp, "Kubernetes event timestamp");
    const count = input.series?.count ?? input.deprecatedCount ?? input.count ?? 1;
    const result = this.db.prepare(`
      INSERT OR IGNORE INTO k8s_events (
        cluster_id, event_uid, event_at, namespace, regarding_uid, regarding_kind,
        regarding_name, reason, type, note, reporting_controller, count
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      clusterId, metadata.uid ?? "", eventAt, metadata.namespace ?? regarding.namespace ?? "",
      regarding.uid ?? "", regarding.kind ?? "", regarding.name ?? "", input.reason ?? "",
      input.type ?? "", input.note ?? input.message ?? "", input.reportingController ?? input.source?.component ?? "", count
    );
    return { inserted: result.changes > 0 };
  }

  recordMetricSample(clusterId, metricName, sampledAt, value, labels = {}) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return { inserted: false, ignored: "non-finite value" };
    const result = this.db.prepare(`
      INSERT OR IGNORE INTO metric_samples (cluster_id, metric_name, sampled_at, value, labels_json)
      VALUES (?, ?, ?, ?, ?)
    `).run(clusterId, metricName, toIso(sampledAt, "sampledAt"), numeric, stableStringify(labels));
    return { inserted: result.changes > 0 };
  }

  attachEmbedding(changeId, embedding) {
    this.db.prepare("UPDATE changes SET embedding_json = ? WHERE id = ?").run(stableStringify(embedding), changeId);
  }

  pendingEmbeddings(limit = 32) {
    return this.db.prepare(`
      SELECT id, memory_text AS memoryText FROM changes
      WHERE embedding_json IS NULL ORDER BY id ASC LIMIT ?
    `).all(Math.min(Math.max(Number(limit) || 32, 1), 256));
  }

  stateAt({ clusterId, timestamp, namespace, kinds, resourceKey } = {}) {
    if (!clusterId) throw new Error("clusterId is required");
    const at = toIso(timestamp, "timestamp");
    const rows = this.db.prepare(`
      SELECT id, resource_key, action, object_json
      FROM changes
      WHERE cluster_id = ? AND authoritative = 1 AND event_at <= ?
      ORDER BY event_at ASC, id ASC
    `).all(clusterId, at);
    const state = new Map();
    for (const row of rows) {
      if (row.action === "DELETED" || row.object_json === null) state.delete(row.resource_key);
      else state.set(row.resource_key, parseJson(row.object_json));
    }
    let objects = [...state.entries()]
      .filter(([key]) => !resourceKey || key === resourceKey)
      .map(([, object]) => object);
    if (namespace !== undefined && namespace !== null && namespace !== "") {
      objects = objects.filter((object) => (object.metadata?.namespace ?? "") === namespace);
    }
    if (kinds?.length) {
      const wanted = new Set(kinds.map(String));
      objects = objects.filter((object) => wanted.has(object.kind));
    }
    objects.sort((left, right) => objectIdentity(left).key.localeCompare(objectIdentity(right).key));
    return { clusterId, timestamp: at, objectCount: objects.length, objects };
  }

  diffStates({ clusterId, from, to, namespace, kinds } = {}) {
    if (!from || !to) throw new Error("from and to are required");
    const before = this.stateAt({ clusterId, timestamp: from, namespace, kinds });
    const after = this.stateAt({ clusterId, timestamp: to, namespace, kinds });
    const left = new Map(before.objects.map((object) => [objectIdentity(object).key, object]));
    const right = new Map(after.objects.map((object) => [objectIdentity(object).key, object]));
    const changes = [];
    for (const key of [...new Set([...left.keys(), ...right.keys()])].sort()) {
      const previous = left.get(key);
      const current = right.get(key);
      const patch = diffJson(previous, current);
      if (patch.length) {
        changes.push({
          resourceKey: key,
          changeType: previous === undefined ? "created" : current === undefined ? "deleted" : "modified",
          changedPaths: changedPaths(patch),
          patch
        });
      }
    }
    return { clusterId, from: before.timestamp, to: after.timestamp, changeCount: changes.length, changes };
  }

  graphAt({ clusterId, timestamp, namespace } = {}) {
    const state = this.stateAt({ clusterId, timestamp, namespace });
    return { ...state, graph: buildStateGraph(state.objects) };
  }

  traceResource({ clusterId, timestamp, resourceKey, maxDepth = 2 } = {}) {
    if (!resourceKey) throw new Error("resourceKey is required");
    const depth = Math.min(Math.max(Number(maxDepth) || 0, 0), 8);
    const state = this.stateAt({ clusterId, timestamp });
    const graph = buildStateGraph(state.objects);
    return { clusterId, timestamp: state.timestamp, resourceKey, ...traceGraph(graph, resourceKey, depth) };
  }

  contextShard({ clusterId, incidentAt, lookback = 900, namespace = "", resourceKey = "", queryEmbedding = null, limit = 100 } = {}) {
    if (!clusterId) throw new Error("clusterId is required");
    const end = toIso(incidentAt, "incidentAt");
    const seconds = parseDurationSeconds(lookback, 900);
    const start = subtractSeconds(end, seconds);
    const namespaceClause = namespace ? " AND namespace = ?" : "";
    const tracedKeys = resourceKey
      ? this.traceResource({ clusterId, timestamp: end, resourceKey, maxDepth: 2 }).nodes.map((node) => node.key)
      : [];
    const relatedKeys = resourceKey && tracedKeys.length === 0 ? [resourceKey] : tracedKeys;
    const resourceClause = relatedKeys.length ? ` AND resource_key IN (${relatedKeys.map(() => "?").join(",")})` : "";
    const parameters = [clusterId, start, end];
    if (namespace) parameters.push(namespace);
    parameters.push(...relatedKeys);
    parameters.push(Math.min(Number(limit) || 100, 500));
    const changeRows = this.db.prepare(`
      SELECT * FROM changes
      WHERE cluster_id = ? AND event_at >= ? AND event_at <= ?${namespaceClause}${resourceClause}
      ORDER BY event_at DESC, id DESC LIMIT ?
    `).all(...parameters);

    const auditRows = this.db.prepare(`
      SELECT audit_id, event_at, stage, verb, actor, source_ips_json, resource_key, namespace, name, response_code
      FROM audit_events WHERE cluster_id = ? AND event_at >= ? AND event_at <= ?${namespace ? " AND namespace = ?" : ""}
      ORDER BY event_at DESC LIMIT 100
    `).all(...(namespace ? [clusterId, start, end, namespace] : [clusterId, start, end]));
    const eventRows = this.db.prepare(`
      SELECT event_at, namespace, regarding_uid, regarding_kind, regarding_name, reason, type, note, reporting_controller, count
      FROM k8s_events WHERE cluster_id = ? AND event_at >= ? AND event_at <= ?${namespace ? " AND namespace = ?" : ""}
      ORDER BY event_at DESC LIMIT 100
    `).all(...(namespace ? [clusterId, start, end, namespace] : [clusterId, start, end]));
    const metricRows = this.db.prepare(`
      SELECT metric_name, sampled_at, value, labels_json
      FROM metric_samples WHERE cluster_id = ? AND sampled_at >= ? AND sampled_at <= ?
      ORDER BY sampled_at DESC LIMIT 250
    `).all(clusterId, start, end);

    const changes = changeRows.map((row) => {
      const event = eventRow(row);
      const ageSeconds = Math.max(0, (new Date(end) - new Date(row.event_at)) / 1000);
      const temporalScore = Math.exp(-ageSeconds / Math.max(seconds / 2, 1));
      const kindBoost = ["ConfigMap", "Secret", "Deployment", "StatefulSet", "DaemonSet"].includes(row.kind) ? 0.15 : 0;
      const actionBoost = row.action === "MODIFIED" ? 0.1 : 0;
      const semanticScore = queryEmbedding ? cosineSimilarity(queryEmbedding, parseJson(row.embedding_json, [])) : 0;
      return { ...event, relevance: Number((temporalScore * 0.55 + semanticScore * 0.35 + kindBoost + actionBoost).toFixed(4)) };
    }).sort((left, right) => right.relevance - left.relevance);

    return {
      clusterId,
      window: { start, incidentAt: end, lookbackSeconds: seconds },
      filters: { namespace, resourceKey, relatedResourceKeys: relatedKeys },
      changes,
      auditEvents: auditRows.map((row) => ({
        auditId: row.audit_id, eventAt: row.event_at, stage: row.stage, verb: row.verb,
        actor: row.actor, sourceIps: parseJson(row.source_ips_json, []), resourceKey: row.resource_key,
        namespace: row.namespace, name: row.name, responseCode: row.response_code
      })),
      kubernetesEvents: eventRows.map((row) => ({
        eventAt: row.event_at, namespace: row.namespace, regardingUid: row.regarding_uid,
        regardingKind: row.regarding_kind, regardingName: row.regarding_name, reason: row.reason,
        type: row.type, note: row.note, reportingController: row.reporting_controller, count: row.count
      })),
      metrics: metricRows.map((row) => ({
        metricName: row.metric_name, sampledAt: row.sampled_at, value: row.value, labels: parseJson(row.labels_json, {})
      })),
      caveats: [
        "State is only reconstructable after this collector first observed an object.",
        "Event order uses source timestamps when present and collector receipt time otherwise.",
        "Relevance ranks evidence; it does not assert causality."
      ]
    };
  }

  prune(retentionDays) {
    const cutoff = subtractSeconds(new Date().toISOString(), retentionDays * 86400);
    const tables = [["changes", "event_at"], ["audit_events", "event_at"], ["k8s_events", "event_at"], ["metric_samples", "sampled_at"]];
    const deleted = {};
    for (const [table, column] of tables) {
      deleted[table] = this.db.prepare(`DELETE FROM ${table} WHERE ${column} < ?`).run(cutoff).changes;
    }
    return { cutoff, deleted };
  }

  stats() {
    const count = (table) => this.db.prepare(`SELECT COUNT(*) AS count FROM ${table}`).get().count;
    return {
      changes: count("changes"),
      auditEvents: count("audit_events"),
      kubernetesEvents: count("k8s_events"),
      metricSamples: count("metric_samples")
    };
  }
}
