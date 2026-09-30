from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .resource_graph import build_state_graph, trace_graph
from .utils import changed_paths, diff_json, object_identity, parse_duration_seconds, sanitize_object, stable_json, subtract_seconds, to_iso


def _loads(value, fallback=None):
    if value in (None, ""):
        return fallback
    return json.loads(value)


def _cosine(left, right) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


class TemporalStore:
    def __init__(self, db_path: str = ":memory:"):
        if db_path != ":memory:":
            Path(db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;")
        self._migrate()

    def _migrate(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS changes (
          id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_id TEXT NOT NULL, source TEXT NOT NULL,
          authoritative INTEGER NOT NULL DEFAULT 1, observed_at TEXT NOT NULL, event_at TEXT NOT NULL,
          action TEXT NOT NULL, resource_key TEXT NOT NULL, api_version TEXT NOT NULL, kind TEXT NOT NULL,
          namespace TEXT NOT NULL DEFAULT '', name TEXT NOT NULL, uid TEXT NOT NULL DEFAULT '',
          resource_version TEXT NOT NULL DEFAULT '', object_json TEXT, patch_json TEXT NOT NULL,
          summary TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '', audit_id TEXT NOT NULL DEFAULT '',
          memory_text TEXT NOT NULL, embedding_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_changes_time ON changes(cluster_id,event_at,id);
        CREATE INDEX IF NOT EXISTS idx_changes_resource ON changes(cluster_id,resource_key,event_at,id);
        CREATE TABLE IF NOT EXISTS audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_id TEXT NOT NULL, audit_id TEXT NOT NULL,
          event_at TEXT NOT NULL, stage TEXT NOT NULL DEFAULT '', verb TEXT NOT NULL DEFAULT '',
          actor TEXT NOT NULL DEFAULT '', source_ips_json TEXT NOT NULL DEFAULT '[]',
          resource_key TEXT NOT NULL DEFAULT '', namespace TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '',
          response_code INTEGER, request_json TEXT, response_json TEXT, UNIQUE(cluster_id,audit_id,stage)
        );
        CREATE TABLE IF NOT EXISTS k8s_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_id TEXT NOT NULL, event_uid TEXT NOT NULL DEFAULT '',
          event_at TEXT NOT NULL, namespace TEXT NOT NULL DEFAULT '', regarding_uid TEXT NOT NULL DEFAULT '',
          regarding_kind TEXT NOT NULL DEFAULT '', regarding_name TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
          type TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', reporting_controller TEXT NOT NULL DEFAULT '',
          count INTEGER NOT NULL DEFAULT 1, UNIQUE(cluster_id,event_uid,event_at,reason,count)
        );
        CREATE TABLE IF NOT EXISTS metric_samples (
          id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_id TEXT NOT NULL, metric_name TEXT NOT NULL,
          sampled_at TEXT NOT NULL, value REAL NOT NULL, labels_json TEXT NOT NULL DEFAULT '{}',
          UNIQUE(cluster_id,metric_name,sampled_at,labels_json)
        );
        CREATE TABLE IF NOT EXISTS benchmark_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, comparison_id TEXT NOT NULL, cluster_id TEXT NOT NULL,
          scenario TEXT NOT NULL DEFAULT '', flow TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
          status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT NOT NULL, total_ms REAL,
          evidence_ms REAL, model_ms REAL, tool_ms REAL, tool_calls INTEGER NOT NULL DEFAULT 0,
          confidence REAL, diagnosis_json TEXT NOT NULL DEFAULT '{}', error_code TEXT NOT NULL DEFAULT '',
          route TEXT NOT NULL DEFAULT '', decision TEXT NOT NULL DEFAULT '', review_status TEXT NOT NULL DEFAULT '',
          stage_metrics_json TEXT NOT NULL DEFAULT '{}', trace_id TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_benchmark_time ON benchmark_runs(cluster_id,finished_at);
        """)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(benchmark_runs)")}
        if "trace_id" not in columns:
            self.db.execute("ALTER TABLE benchmark_runs ADD COLUMN trace_id TEXT NOT NULL DEFAULT ''")

    def close(self):
        with self.lock:
            self.db.close()

    @staticmethod
    def _summary(action: str, identity: dict, patch: list[dict]) -> str:
        location = f"{identity['namespace']}/{identity['name']}" if identity["namespace"] else identity["name"]
        paths = ", ".join((item.get("path") or "(root)") for item in patch[:4])
        remainder = f" (+{len(patch)-4})" if len(patch) > 4 else ""
        return f"{action} {identity['kind']} {location}" + (f": {paths}{remainder}" if patch else "")

    @staticmethod
    def _memory_text(action: str, identity: dict, patch: list[dict], actor: str, event_at: str) -> str:
        operations = [f"{item['op']} {item.get('path') or '(root)'} {stable_json(item.get('value'))[:240]}" for item in patch]
        return " ".join(filter(None, [event_at, action, identity["kind"], identity["namespace"], identity["name"], f"actor={actor}" if actor else "", *operations]))

    def _recompute_patch(self, row, previous):
        if row is None:
            return
        before = _loads(previous["object_json"]) if previous and previous["object_json"] is not None else None
        after = _loads(row["object_json"]) if row["object_json"] is not None else None
        before_missing = previous is None or previous["object_json"] is None
        after_missing = row["object_json"] is None
        if before_missing and not after_missing:
            patch = diff_json(after=after)
        elif after_missing and not before_missing:
            patch = diff_json(before=before)
        else:
            patch = diff_json(before, after)
        identity = {"kind": row["kind"], "namespace": row["namespace"], "name": row["name"]}
        summary = self._summary(row["action"], identity, patch)
        text = self._memory_text(row["action"], identity, patch, row["actor"], row["event_at"])
        self.db.execute("UPDATE changes SET patch_json=?,summary=?,memory_text=?,embedding_json=CASE WHEN memory_text=? THEN embedding_json ELSE NULL END WHERE id=?", (stable_json(patch), summary, text, text, row["id"]))

    def record_resource_change(self, *, cluster_id: str, action: str, object: dict, source="watch", observed_at=None, event_at=None, actor="", audit_id="", embedding=None):
        if not cluster_id:
            raise ValueError("cluster_id is required")
        normalized = str(action or "MODIFIED").upper()
        sanitized = sanitize_object(object)
        identity = object_identity(sanitized)
        observed = to_iso(observed_at, "observed_at")
        effective = to_iso(event_at or observed, "event_at")
        with self.lock:
            if identity["resourceVersion"]:
                duplicate = self.db.execute("SELECT id FROM changes WHERE cluster_id=? AND source=? AND resource_key=? AND resource_version=? AND action=? LIMIT 1", (cluster_id, source, identity["key"], identity["resourceVersion"], normalized)).fetchone()
                if duplicate:
                    return {"id": duplicate["id"], "duplicate": True}
            previous = self.db.execute("SELECT * FROM changes WHERE cluster_id=? AND resource_key=? AND authoritative=1 AND event_at<=? ORDER BY event_at DESC,id DESC LIMIT 1", (cluster_id, identity["key"], effective)).fetchone()
            before = _loads(previous["object_json"]) if previous and previous["object_json"] is not None else None
            after = None if normalized == "DELETED" else sanitized
            patch = diff_json(after=after) if previous is None and after is not None else diff_json(before=before) if after is None and previous else diff_json(before, after)
            summary = self._summary(normalized, identity, patch)
            text = self._memory_text(normalized, identity, patch, actor, effective)
            cursor = self.db.execute("""INSERT INTO changes(cluster_id,source,authoritative,observed_at,event_at,action,resource_key,api_version,kind,namespace,name,uid,resource_version,object_json,patch_json,summary,actor,audit_id,memory_text,embedding_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                cluster_id, source, 1, observed, effective, normalized, identity["key"], identity["apiVersion"], identity["kind"], identity["namespace"], identity["name"], identity["uid"], identity["resourceVersion"], None if after is None else stable_json(after), stable_json(patch), summary, actor, audit_id, text, stable_json(embedding) if embedding else None,
            ))
            inserted_id = cursor.lastrowid
            current = self.db.execute("SELECT * FROM changes WHERE id=?", (inserted_id,)).fetchone()
            previous = self.db.execute("SELECT * FROM changes WHERE cluster_id=? AND resource_key=? AND (event_at<? OR (event_at=? AND id<?)) ORDER BY event_at DESC,id DESC LIMIT 1", (cluster_id, identity["key"], effective, effective, inserted_id)).fetchone()
            following = self.db.execute("SELECT * FROM changes WHERE cluster_id=? AND resource_key=? AND (event_at>? OR (event_at=? AND id>?)) ORDER BY event_at,id LIMIT 1", (cluster_id, identity["key"], effective, effective, inserted_id)).fetchone()
            self._recompute_patch(current, previous)
            self._recompute_patch(following, current)
            repaired = self.db.execute("SELECT patch_json,summary FROM changes WHERE id=?", (inserted_id,)).fetchone()
            return {"id": inserted_id, "duplicate": False, "identity": identity, "patch": _loads(repaired["patch_json"], []), "summary": repaired["summary"]}

    def record_kubernetes_event(self, cluster_id: str, event: dict):
        metadata = event.get("metadata", {})
        regarding = event.get("regarding") or event.get("involvedObject") or {}
        event_at = to_iso(event.get("eventTime") or event.get("series", {}).get("lastObservedTime") or event.get("deprecatedLastTimestamp") or event.get("lastTimestamp") or metadata.get("creationTimestamp"), "Kubernetes event timestamp")
        count = event.get("series", {}).get("count") or event.get("deprecatedCount") or event.get("count") or 1
        with self.lock:
            cursor = self.db.execute("INSERT OR IGNORE INTO k8s_events(cluster_id,event_uid,event_at,namespace,regarding_uid,regarding_kind,regarding_name,reason,type,note,reporting_controller,count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                cluster_id, metadata.get("uid", ""), event_at, metadata.get("namespace") or regarding.get("namespace", ""), regarding.get("uid", ""), regarding.get("kind", ""), regarding.get("name", ""), event.get("reason", ""), event.get("type", ""), event.get("note") or event.get("message", ""), event.get("reportingController") or event.get("source", {}).get("component", ""), count,
            ))
        return {"inserted": cursor.rowcount > 0}

    def record_audit_event(self, cluster_id: str, event: dict):
        reference = event.get("objectRef", {})
        api_version = reference.get("apiVersion", "v1")
        if reference.get("apiGroup") and "/" not in api_version:
            api_version = f"{reference['apiGroup']}/{api_version}"
        resource = reference.get("resource", "Unknown")
        kind = (event.get("responseObject") or event.get("requestObject") or {}).get("kind") or resource.rstrip("s").title()
        key = f"{api_version}|{kind}|{reference.get('namespace') or '_cluster'}|{reference.get('name')}" if reference.get("name") else ""
        event_at = to_iso(event.get("stageTimestamp") or event.get("requestReceivedTimestamp"), "audit timestamp")
        with self.lock:
            cursor = self.db.execute("INSERT OR IGNORE INTO audit_events(cluster_id,audit_id,event_at,stage,verb,actor,source_ips_json,resource_key,namespace,name,response_code,request_json,response_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                cluster_id, event.get("auditID", ""), event_at, event.get("stage", ""), event.get("verb", ""), event.get("user", {}).get("username", ""), stable_json(event.get("sourceIPs", [])), key, reference.get("namespace", ""), reference.get("name", ""), event.get("responseStatus", {}).get("code"), stable_json(sanitize_object(event["requestObject"])) if event.get("requestObject") else None, stable_json(sanitize_object(event["responseObject"])) if event.get("responseObject") else None,
            ))
        return {"inserted": cursor.rowcount > 0, "auditId": event.get("auditID", ""), "resourceKey": key}

    def record_metric_sample(self, cluster_id, metric_name, sampled_at, value, labels=None):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return {"inserted": False, "ignored": "non-finite value"}
        if not math.isfinite(numeric):
            return {"inserted": False, "ignored": "non-finite value"}
        with self.lock:
            cursor = self.db.execute("INSERT OR IGNORE INTO metric_samples(cluster_id,metric_name,sampled_at,value,labels_json) VALUES(?,?,?,?,?)", (cluster_id, metric_name, to_iso(sampled_at), numeric, stable_json(labels or {})))
        return {"inserted": cursor.rowcount > 0}

    def state_at(self, *, cluster_id, timestamp=None, namespace=None, kinds=None, resource_key=None):
        at = to_iso(timestamp)
        with self.lock:
            rows = self.db.execute("SELECT resource_key,action,object_json FROM changes WHERE cluster_id=? AND authoritative=1 AND event_at<=? ORDER BY event_at,id", (cluster_id, at)).fetchall()
        state = {}
        for row in rows:
            if row["action"] == "DELETED" or row["object_json"] is None:
                state.pop(row["resource_key"], None)
            else:
                state[row["resource_key"]] = _loads(row["object_json"])
        objects = [obj for key, obj in state.items() if not resource_key or key == resource_key]
        if namespace:
            objects = [obj for obj in objects if obj.get("metadata", {}).get("namespace", "") == namespace]
        if kinds:
            objects = [obj for obj in objects if obj.get("kind") in set(kinds)]
        objects.sort(key=lambda obj: object_identity(obj)["key"])
        return {"clusterId": cluster_id, "timestamp": at, "objectCount": len(objects), "objects": objects}

    def diff_states(self, *, cluster_id, from_at, to_at, namespace=None, kinds=None):
        before = self.state_at(cluster_id=cluster_id, timestamp=from_at, namespace=namespace, kinds=kinds)
        after = self.state_at(cluster_id=cluster_id, timestamp=to_at, namespace=namespace, kinds=kinds)
        left = {object_identity(obj)["key"]: obj for obj in before["objects"]}
        right = {object_identity(obj)["key"]: obj for obj in after["objects"]}
        changes = []
        for key in sorted(set(left) | set(right)):
            previous, current = left.get(key), right.get(key)
            patch = diff_json(after=current) if previous is None else diff_json(before=previous) if current is None else diff_json(previous, current)
            if patch:
                changes.append({"resourceKey": key, "changeType": "created" if previous is None else "deleted" if current is None else "modified", "changedPaths": changed_paths(patch), "patch": patch})
        return {"clusterId": cluster_id, "from": before["timestamp"], "to": after["timestamp"], "changeCount": len(changes), "changes": changes}

    def graph_at(self, *, cluster_id, timestamp=None, namespace=None):
        state = self.state_at(cluster_id=cluster_id, timestamp=timestamp, namespace=namespace)
        return {**state, "graph": build_state_graph(state["objects"])}

    def trace_resource(self, *, cluster_id, timestamp, resource_key, max_depth=2):
        state = self.state_at(cluster_id=cluster_id, timestamp=timestamp)
        return {"clusterId": cluster_id, "timestamp": state["timestamp"], "resourceKey": resource_key, **trace_graph(build_state_graph(state["objects"]), resource_key, min(max(int(max_depth), 0), 8))}

    def context_shard(self, *, cluster_id, incident_at, lookback=900, namespace="", resource_key="", query_embedding=None, limit=100):
        end = to_iso(incident_at, "incident_at")
        seconds = parse_duration_seconds(lookback, 900)
        start = subtract_seconds(end, seconds)
        filters = (" AND namespace=?" if namespace else "") + (" AND resource_key=?" if resource_key else "")
        filter_params = ([namespace] if namespace else []) + ([resource_key] if resource_key else [])
        params = [cluster_id, start, end] + filter_params + [min(max(int(limit or 100), 1), 500)]
        with self.lock:
            changes = self.db.execute(f"SELECT * FROM changes WHERE cluster_id=? AND event_at>=? AND event_at<=?{filters} ORDER BY event_at DESC,id DESC LIMIT ?", params).fetchall()
            event_filter = " AND namespace=?" if namespace else ""
            events = self.db.execute(f"SELECT * FROM k8s_events WHERE cluster_id=? AND event_at>=? AND event_at<=?{event_filter} ORDER BY event_at DESC LIMIT 100", [cluster_id, start, end] + ([namespace] if namespace else [])).fetchall()
            audits = self.db.execute(f"SELECT * FROM audit_events WHERE cluster_id=? AND event_at>=? AND event_at<=?{filters} ORDER BY event_at DESC LIMIT 100", [cluster_id, start, end] + filter_params).fetchall()
            metrics = self.db.execute("SELECT * FROM metric_samples WHERE cluster_id=? AND sampled_at>=? AND sampled_at<=? ORDER BY sampled_at DESC LIMIT 250", (cluster_id, start, end)).fetchall()
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        ranked = []
        for row in changes:
            age = max(0, (end_dt - datetime.fromisoformat(row["event_at"].replace("Z", "+00:00"))).total_seconds())
            relevance = math.exp(-age / max(seconds / 2, 1)) * 0.55
            relevance += 0.15 if row["kind"] in {"ConfigMap", "Secret", "Deployment", "StatefulSet", "DaemonSet"} else 0
            relevance += 0.1 if row["action"] == "MODIFIED" else 0
            if query_embedding:
                relevance += _cosine(query_embedding, _loads(row["embedding_json"], [])) * 0.35
            ranked.append({
                "id": row["id"], "source": row["source"], "observedAt": row["observed_at"], "eventAt": row["event_at"],
                "action": row["action"], "resourceKey": row["resource_key"], "apiVersion": row["api_version"], "kind": row["kind"],
                "namespace": row["namespace"], "name": row["name"], "patch": _loads(row["patch_json"], []), "summary": row["summary"],
                "actor": row["actor"], "relevance": round(relevance, 4),
            })
        ranked.sort(key=lambda item: item["relevance"], reverse=True)
        return {
            "clusterId": cluster_id,
            "window": {"start": start, "incidentAt": end, "lookbackSeconds": seconds},
            "filters": {"namespace": namespace, "resourceKey": resource_key},
            "changes": ranked,
            "auditEvents": [{"auditId": row["audit_id"], "eventAt": row["event_at"], "stage": row["stage"], "verb": row["verb"], "actor": row["actor"], "sourceIps": _loads(row["source_ips_json"], []), "resourceKey": row["resource_key"], "namespace": row["namespace"], "name": row["name"], "responseCode": row["response_code"]} for row in audits],
            "kubernetesEvents": [{"eventAt": row["event_at"], "namespace": row["namespace"], "regardingKind": row["regarding_kind"], "regardingName": row["regarding_name"], "reason": row["reason"], "type": row["type"], "note": row["note"], "reportingController": row["reporting_controller"], "count": row["count"]} for row in events],
            "metrics": [{"metricName": row["metric_name"], "sampledAt": row["sampled_at"], "value": row["value"], "labels": _loads(row["labels_json"], {})} for row in metrics],
            "caveats": ["State is only reconstructable after this collector first observed an object.", "Event order uses source timestamps when present and collector receipt time otherwise.", "Relevance ranks evidence; it does not assert causality."],
        }

    def timeline(self, *, cluster_id, namespace="", limit=500):
        bounded = min(max(int(limit), 1), 2000)
        namespace_filter = " AND namespace=?" if namespace else ""
        params = [cluster_id] + ([namespace] if namespace else []) + [bounded]
        with self.lock:
            changes = self.db.execute(
                f"SELECT event_at,action,resource_key,kind,namespace,name,summary,patch_json FROM changes WHERE cluster_id=?{namespace_filter} ORDER BY event_at DESC,id DESC LIMIT ?",
                params,
            ).fetchall()
            events = self.db.execute(
                f"SELECT event_at,type,reason,namespace,regarding_kind,regarding_name,note,count FROM k8s_events WHERE cluster_id=?{namespace_filter} ORDER BY event_at DESC,id DESC LIMIT ?",
                params,
            ).fetchall()
        entries = [
            {
                "at": row["event_at"], "entryType": "change", "severity": "change", "action": row["action"],
                "resourceKey": row["resource_key"], "kind": row["kind"], "namespace": row["namespace"],
                "name": row["name"], "title": row["summary"], "patch": _loads(row["patch_json"], []),
            }
            for row in changes
        ]
        entries.extend(
            {
                "at": row["event_at"], "entryType": "event", "severity": "warning" if row["type"] == "Warning" else "normal",
                "action": row["reason"], "resourceKey": f"event|{row['regarding_kind']}|{row['namespace'] or '_cluster'}|{row['regarding_name']}",
                "kind": row["regarding_kind"], "namespace": row["namespace"], "name": row["regarding_name"],
                "title": f"{row['reason']} · {row['regarding_kind']}/{row['regarding_name']}", "note": row["note"], "count": row["count"],
            }
            for row in events
        )
        entries.sort(key=lambda item: item["at"])
        return {
            "clusterId": cluster_id,
            "namespace": namespace,
            "start": entries[0]["at"] if entries else None,
            "end": entries[-1]["at"] if entries else None,
            "entryCount": len(entries),
            "entries": entries[-bounded:],
        }

    def record_benchmark_run(self, **values):
        diagnosis = {k: min(max(float(v), 0), 1) for k, v in values.get("diagnosis", {}).items() if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", k)}
        stages = {k: float(v) for k, v in values.get("stage_metrics", {}).items() if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", k) and float(v) >= 0}
        with self.lock:
            cursor = self.db.execute("""INSERT INTO benchmark_runs(comparison_id,cluster_id,scenario,flow,provider,model,status,started_at,finished_at,total_ms,evidence_ms,model_ms,tool_ms,tool_calls,confidence,diagnosis_json,error_code,route,decision,review_status,stage_metrics_json,trace_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                values["comparison_id"], values["cluster_id"], values.get("scenario", ""), values["flow"], values["provider"], values["model"], values["status"], to_iso(values["started_at"]), to_iso(values["finished_at"]), values.get("total_ms"), values.get("evidence_ms"), values.get("model_ms"), values.get("tool_ms"), int(values.get("tool_calls", 0)), values.get("confidence"), stable_json(diagnosis), values.get("error_code", ""), values.get("route", ""), values.get("decision", ""), values.get("review_status", ""), stable_json(stages), values.get("trace_id", ""),
            ))
        return {"id": cursor.lastrowid, "comparisonId": values["comparison_id"]}

    def benchmark_runs(self, *, cluster_id, limit=1000):
        with self.lock:
            rows = self.db.execute("SELECT * FROM benchmark_runs WHERE cluster_id=? ORDER BY finished_at DESC,id DESC LIMIT ?", (cluster_id, min(max(int(limit), 1), 10000))).fetchall()
        return [{"comparisonId": row["comparison_id"], "clusterId": row["cluster_id"], "scenario": row["scenario"], "flow": row["flow"], "provider": row["provider"], "model": row["model"], "status": row["status"], "startedAt": row["started_at"], "finishedAt": row["finished_at"], "totalMs": row["total_ms"], "evidenceMs": row["evidence_ms"], "modelMs": row["model_ms"], "toolMs": row["tool_ms"], "toolCalls": row["tool_calls"], "confidence": row["confidence"], "diagnosis": _loads(row["diagnosis_json"], {}), "errorCode": row["error_code"], "route": row["route"], "decision": row["decision"], "reviewStatus": row["review_status"], "stageMetrics": _loads(row["stage_metrics_json"], {}), "traceId": row["trace_id"]} for row in rows]

    def stats(self):
        with self.lock:
            return {name: self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for name, table in {"changes": "changes", "auditEvents": "audit_events", "kubernetesEvents": "k8s_events", "metricSamples": "metric_samples", "benchmarkRuns": "benchmark_runs"}.items()}

    def prune(self, retention_days: int):
        cutoff = subtract_seconds(to_iso(), retention_days * 86400)
        deleted = {}
        with self.lock:
            for table, column in (("changes", "event_at"), ("audit_events", "event_at"), ("k8s_events", "event_at"), ("metric_samples", "sampled_at"), ("benchmark_runs", "finished_at")):
                cursor = self.db.execute(f"DELETE FROM {table} WHERE {column}<?", (cutoff,))
                deleted[table] = cursor.rowcount
        return {"cutoff": cutoff, "deleted": deleted}
