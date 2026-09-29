import assert from "node:assert/strict";
import test from "node:test";
import { TemporalStore } from "../src/store.mjs";

const clusterId = "test-cluster";

function configMap(timeout, resourceVersion) {
  return {
    apiVersion: "v1",
    kind: "ConfigMap",
    metadata: { namespace: "shop", name: "api-config", uid: "cm-1", resourceVersion },
    data: { DB_TIMEOUT_SECONDS: String(timeout) }
  };
}

function deployment(resourceVersion = "1") {
  return {
    apiVersion: "apps/v1",
    kind: "Deployment",
    metadata: { namespace: "shop", name: "api", uid: "deploy-1", resourceVersion },
    spec: {
      selector: { matchLabels: { app: "api" } },
      template: {
        metadata: { labels: { app: "api" } },
        spec: {
          containers: [{
            name: "api",
            image: "example/api:v1",
            envFrom: [{ configMapRef: { name: "api-config" } }, { secretRef: { name: "db-credentials" } }]
          }]
        }
      }
    }
  };
}

test("rewinds and diffs exact object state", () => {
  const store = new TemporalStore(":memory:");
  store.recordResourceChange({ clusterId, action: "ADDED", object: configMap(30, "1"), eventAt: "2026-09-28T12:00:00Z" });
  store.recordResourceChange({ clusterId, action: "MODIFIED", object: configMap(3, "2"), eventAt: "2026-09-28T12:04:00Z" });

  const before = store.stateAt({ clusterId, timestamp: "2026-09-28T12:03:00Z" });
  const after = store.stateAt({ clusterId, timestamp: "2026-09-28T12:05:00Z" });
  assert.equal(before.objects[0].data.DB_TIMEOUT_SECONDS, "30");
  assert.equal(after.objects[0].data.DB_TIMEOUT_SECONDS, "3");

  const diff = store.diffStates({ clusterId, from: "2026-09-28T12:03:00Z", to: "2026-09-28T12:05:00Z" });
  assert.equal(diff.changeCount, 1);
  assert.deepEqual(diff.changes[0].changedPaths, ["/data/DB_TIMEOUT_SECONDS", "/metadata/resourceVersion"]);
  store.close();
});

test("reconstruction follows event time even when events arrive out of order", () => {
  const store = new TemporalStore(":memory:");
  store.recordResourceChange({ clusterId, action: "MODIFIED", object: configMap(3, "2"), eventAt: "2026-09-28T12:04:00Z" });
  store.recordResourceChange({ clusterId, action: "ADDED", object: configMap(30, "1"), eventAt: "2026-09-28T12:00:00Z" });
  assert.equal(store.stateAt({ clusterId, timestamp: "2026-09-28T12:02:00Z" }).objects[0].data.DB_TIMEOUT_SECONDS, "30");
  assert.equal(store.stateAt({ clusterId, timestamp: "2026-09-28T12:05:00Z" }).objects[0].data.DB_TIMEOUT_SECONDS, "3");
  const later = store.contextShard({ clusterId, incidentAt: "2026-09-28T12:05:00Z", lookback: "2m" })
    .changes.find((change) => change.resourceVersion === "2");
  assert.equal(later.patch[0].path, "/data/DB_TIMEOUT_SECONDS");
  store.close();
});

test("builds graph relationships and a multi-signal incident shard", () => {
  const store = new TemporalStore(":memory:");
  store.recordResourceChange({ clusterId, action: "ADDED", object: configMap(30, "1"), eventAt: "2026-09-28T12:00:00Z" });
  store.recordResourceChange({ clusterId, action: "ADDED", object: deployment(), eventAt: "2026-09-28T12:01:00Z" });
  store.recordResourceChange({ clusterId, action: "MODIFIED", object: configMap(3, "2"), eventAt: "2026-09-28T12:04:00Z", actor: "alice@example.com" });
  store.recordAuditEvent(clusterId, {
    auditID: "audit-1",
    stage: "ResponseComplete",
    stageTimestamp: "2026-09-28T12:04:00Z",
    verb: "update",
    user: { username: "alice@example.com" },
    objectRef: { apiVersion: "v1", resource: "configmaps", namespace: "shop", name: "api-config" },
    responseStatus: { code: 200 }
  });
  store.recordKubernetesEvent(clusterId, {
    metadata: { uid: "event-1", namespace: "shop" },
    eventTime: "2026-09-28T12:06:00Z",
    regarding: { kind: "Pod", name: "api-123", uid: "pod-1" },
    reason: "BackOff",
    type: "Warning",
    note: "Back-off restarting failed container"
  });
  store.recordMetricSample(clusterId, "pod_restarts", "2026-09-28T12:06:30Z", 4, { namespace: "shop", pod: "api-123" });

  const graph = store.graphAt({ clusterId, timestamp: "2026-09-28T12:07:00Z" }).graph;
  assert.ok(graph.edges.some((edge) => edge.type === "reads-config" && edge.to.includes("api-config")));
  assert.ok(graph.edges.some((edge) => edge.type === "reads-secret" && edge.to.includes("db-credentials")));
  assert.equal(graph.nodes.find((node) => node.name === "db-credentials").observed, false);

  const context = store.contextShard({ clusterId, incidentAt: "2026-09-28T12:07:00Z", lookback: "10m", namespace: "shop" });
  assert.equal(context.auditEvents[0].actor, "alice@example.com");
  assert.equal(context.kubernetesEvents[0].reason, "BackOff");
  assert.equal(context.metrics[0].value, 4);
  assert.ok(context.changes.some((change) => change.summary.includes("api-config")));
  store.close();
});

test("redacts Secret values before persistence", () => {
  const store = new TemporalStore(":memory:");
  store.recordResourceChange({
    clusterId,
    action: "ADDED",
    eventAt: "2026-09-28T12:00:00Z",
    object: {
      apiVersion: "v1", kind: "Secret",
      metadata: { namespace: "shop", name: "db", resourceVersion: "1", managedFields: [{ manager: "kubectl" }] },
      data: { password: "c3VwZXJzZWNyZXQ=" }
    }
  });
  const object = store.stateAt({ clusterId, timestamp: "2026-09-28T12:01:00Z" }).objects[0];
  assert.equal(object.data.password, "<redacted>");
  assert.equal(object.metadata.managedFields, undefined);
  store.close();
});

test("uses vectors only to rank time-bounded evidence", () => {
  const store = new TemporalStore(":memory:");
  const first = store.recordResourceChange({ clusterId, action: "ADDED", object: configMap(30, "1"), eventAt: "2026-09-28T12:00:00Z" });
  const second = store.recordResourceChange({ clusterId, action: "MODIFIED", object: configMap(3, "2"), eventAt: "2026-09-28T12:01:00Z" });
  store.attachEmbedding(first.id, [1, 0]);
  store.attachEmbedding(second.id, [0, 1]);
  const context = store.contextShard({
    clusterId,
    incidentAt: "2026-09-28T12:01:01Z",
    lookback: "2m",
    queryEmbedding: [1, 0]
  });
  const withoutVector = store.contextShard({ clusterId, incidentAt: "2026-09-28T12:01:01Z", lookback: "2m" });
  const rankedFirst = context.changes.find((change) => change.id === first.id);
  const unrankedFirst = withoutVector.changes.find((change) => change.id === first.id);
  assert.ok(rankedFirst.relevance > unrankedFirst.relevance);
  assert.equal(store.stateAt({ clusterId, timestamp: "2026-09-28T12:01:01Z" }).objects[0].data.DB_TIMEOUT_SECONDS, "3");
  store.close();
});

test("deletion removes an object from later reconstructed state", () => {
  const store = new TemporalStore(":memory:");
  store.recordResourceChange({ clusterId, action: "ADDED", object: configMap(30, "1"), eventAt: "2026-09-28T12:00:00Z" });
  store.recordResourceChange({ clusterId, action: "DELETED", object: configMap(30, "2"), eventAt: "2026-09-28T12:10:00Z" });
  assert.equal(store.stateAt({ clusterId, timestamp: "2026-09-28T12:09:00Z" }).objectCount, 1);
  assert.equal(store.stateAt({ clusterId, timestamp: "2026-09-28T12:11:00Z" }).objectCount, 0);
  store.recordResourceChange({ clusterId, action: "ADDED", object: configMap(10, "3"), eventAt: "2026-09-28T12:12:00Z" });
  const recreated = store.contextShard({ clusterId, incidentAt: "2026-09-28T12:13:00Z", lookback: "2m" });
  assert.equal(recreated.changes[0].patch[0].op, "add");
  store.close();
});

test("stores bounded incident-pipeline routing and per-stage latency", () => {
  const store = new TemporalStore(":memory:");
  store.recordBenchmarkRun({
    comparisonId: "pipeline-test",
    clusterId,
    scenario: "synthetic",
    flow: "incident_pipeline",
    provider: "local",
    model: "english+qwen-test",
    status: "success",
    startedAt: "2026-09-29T12:00:00Z",
    finishedAt: "2026-09-29T12:00:01Z",
    route: "qwen_investigation",
    decision: "bad_image_rollout",
    reviewStatus: "awaiting_human_review",
    stageMetrics: { laya_input_guardrail: 1.25, qwen_inference: 900, "invalid stage": 100 }
  });
  const run = store.benchmarkRuns({ clusterId })[0];
  assert.equal(run.route, "qwen_investigation");
  assert.equal(run.reviewStatus, "awaiting_human_review");
  assert.deepEqual(run.stageMetrics, { laya_input_guardrail: 1.25, qwen_inference: 900 });
  store.close();
});
