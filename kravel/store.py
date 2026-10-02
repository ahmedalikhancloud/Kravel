from __future__ import annotations

import json
import sqlite3
import threading
import uuid

from .utils import stable_json, to_iso


class AuditStore:
    """Append-only audit storage for debugger, approval, and execution activity."""

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    def _migrate(self):
        with self.lock, self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_entries (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  at TEXT NOT NULL,
                  component TEXT NOT NULL,
                  action TEXT NOT NULL,
                  actor TEXT NOT NULL,
                  resource TEXT NOT NULL,
                  outcome TEXT NOT NULL,
                  duration_ms REAL,
                  trace_id TEXT NOT NULL,
                  details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_entries(at DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_entries(action, at DESC);

                CREATE TABLE IF NOT EXISTS investigations (
                  id TEXT PRIMARY KEY,
                  started_at TEXT NOT NULL,
                  finished_at TEXT NOT NULL,
                  namespace TEXT NOT NULL,
                  status TEXT NOT NULL,
                  total_ms REAL NOT NULL,
                  model_ms REAL NOT NULL,
                  tool_ms REAL NOT NULL,
                  tool_calls INTEGER NOT NULL,
                  input_guardrail_ms REAL NOT NULL,
                  output_guardrail_ms REAL NOT NULL,
                  mlflow_setup_ms REAL NOT NULL DEFAULT 0,
                  mlflow_overhead_ms REAL NOT NULL DEFAULT 0,
                  mlflow_flush_ms REAL NOT NULL DEFAULT 0,
                  trace_id TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS proposals (
                  id TEXT PRIMARY KEY,
                  created_at TEXT NOT NULL,
                  expires_at TEXT NOT NULL,
                  fix_id TEXT NOT NULL,
                  namespace TEXT NOT NULL,
                  resource TEXT NOT NULL,
                  status TEXT NOT NULL,
                  command TEXT NOT NULL,
                  dry_run_json TEXT NOT NULL,
                  approved_at TEXT NOT NULL DEFAULT '',
                  approval_actor TEXT NOT NULL DEFAULT '',
                  executed_at TEXT NOT NULL DEFAULT '',
                  result_json TEXT NOT NULL DEFAULT '{}',
                  slack_channel TEXT NOT NULL DEFAULT '',
                  slack_ts TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_proposals_created ON proposals(created_at DESC);
                CREATE TABLE IF NOT EXISTS workflows (
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, namespace TEXT NOT NULL,
                  target TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
                  finished_at TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS workflow_steps (
                  run_id TEXT NOT NULL, step_key TEXT NOT NULL, ordinal INTEGER NOT NULL,
                  label TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
                  finished_at TEXT NOT NULL DEFAULT '', duration_ms REAL NOT NULL DEFAULT 0,
                  details_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(run_id, step_key)
                );
                CREATE TABLE IF NOT EXISTS presentation_state (
                  key TEXT PRIMARY KEY, value_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cluster_plans (
                  run_id TEXT NOT NULL, plan_id TEXT NOT NULL, plan_json TEXT NOT NULL,
                  PRIMARY KEY(run_id, plan_id)
                );
                """
            )
            columns = {row[1] for row in self.connection.execute("PRAGMA table_info(investigations)").fetchall()}
            for column in ("mlflow_setup_ms", "mlflow_overhead_ms", "mlflow_flush_ms"):
                if column not in columns:
                    self.connection.execute(f"ALTER TABLE investigations ADD COLUMN {column} REAL NOT NULL DEFAULT 0")

    def close(self):
        with self.lock:
            self.connection.close()

    def save_cluster_plan(self, run_id, draft):
        with self.lock, self.connection:
            self.connection.execute("INSERT OR IGNORE INTO cluster_plans VALUES (?, ?, ?)", (run_id, draft["id"], stable_json(draft)))

    def cluster_plan(self, run_id, plan_id):
        with self.lock:
            row = self.connection.execute("SELECT plan_json FROM cluster_plans WHERE run_id=? AND plan_id=?", (run_id, plan_id)).fetchone()
        return json.loads(row[0]) if row else None

    def demo_session(self):
        """A persisted display boundary, not deletion of audit or trace history."""
        with self.lock, self.connection:
            row = self.connection.execute("SELECT value_json FROM presentation_state WHERE key='demo_session'").fetchone()
            if row:
                return json.loads(row[0])
            return self.start_demo_session()

    def start_demo_session(self):
        value = {"id": str(uuid.uuid4()), "startedAt": to_iso()}
        with self.lock, self.connection:
            self.connection.execute("INSERT OR REPLACE INTO presentation_state VALUES ('demo_session', ?)", (stable_json(value),))
        return value

    def start_workflow(self, run_id, kind, namespace, target=""):
        with self.lock, self.connection:
            self.connection.execute("INSERT OR IGNORE INTO workflows(id,kind,namespace,target,status,started_at) VALUES (?,?,?,?,?,?)", (run_id, kind, namespace, target, "running", to_iso()))
        return self.workflow(run_id)

    def workflow_step(self, run_id, key, label, status, *, duration_ms=0, details=None):
        with self.lock, self.connection:
            previous = self.connection.execute("SELECT * FROM workflow_steps WHERE run_id=? AND step_key=?", (run_id, key)).fetchone()
            ordinal = previous["ordinal"] if previous else self.connection.execute("SELECT COUNT(*) FROM workflow_steps WHERE run_id=?", (run_id,)).fetchone()[0]
            self.connection.execute("INSERT OR REPLACE INTO workflow_steps VALUES (?,?,?,?,?,?,?,?,?)", (run_id, key, ordinal, label, status, previous["started_at"] if previous else to_iso(), "" if status == "running" else to_iso(), duration_ms, stable_json(details or {})))

    def update_workflow(self, run_id, *, status=None, payload=None):
        with self.lock, self.connection:
            if status:
                self.connection.execute("UPDATE workflows SET status=?, finished_at=? WHERE id=?", (status, "" if status == "running" else to_iso(), run_id))
            if payload is not None:
                self.connection.execute("UPDATE workflows SET payload_json=? WHERE id=?", (stable_json(payload), run_id))
        return self.workflow(run_id)

    def workflow(self, run_id):
        with self.lock:
            row = self.connection.execute("SELECT * FROM workflows WHERE id=?", (run_id,)).fetchone()
            if not row:
                return None
            steps = self.connection.execute("SELECT * FROM workflow_steps WHERE run_id=? ORDER BY ordinal", (run_id,)).fetchall()
        return {**{k: v for k, v in dict(row).items() if k != "payload_json"}, "payload": json.loads(row["payload_json"]), "steps": [{**{k: v for k, v in dict(step).items() if k != "details_json"}, "details": json.loads(step["details_json"])} for step in steps]}

    def workflows(self, kind="investigation", limit=30):
        with self.lock:
            ids = self.connection.execute("SELECT id FROM workflows WHERE kind=? ORDER BY started_at DESC LIMIT ?", (kind, min(max(int(limit), 1), 100))).fetchall()
        return [self.workflow(row["id"]) for row in ids]

    def interrupt_workflows(self, kind):
        for run in self.workflows(kind, 100):
            if run["status"] == "running":
                for step in run["steps"]:
                    if step["status"] == "running":
                        self.workflow_step(run["id"], step["step_key"], step["label"], "interrupted", details={"reason": "Process restarted; no automatic replay."})
                self.update_workflow(run["id"], status="interrupted")

    def workflow_metrics(self):
        with self.lock:
            rows = self.connection.execute("SELECT w.kind, s.step_key, s.status, COUNT(*) count, SUM(s.duration_ms)/1000 seconds FROM workflow_steps s JOIN workflows w ON w.id=s.run_id WHERE s.status != 'running' GROUP BY w.kind,s.step_key,s.status").fetchall()
        return [dict(row) for row in rows]

    def record(self, component: str, action: str, *, actor: str = "system", resource: str = "", outcome: str = "success", duration_ms: float | None = None, trace_id: str = "", details: dict | None = None) -> int:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "INSERT INTO audit_entries(at, component, action, actor, resource, outcome, duration_ms, trace_id, details_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (to_iso(), component, action, actor, resource, outcome, duration_ms, trace_id, stable_json(details or {})),
            )
            return int(cursor.lastrowid)

    def audit_entries(self, limit: int = 200) -> list[dict]:
        limit = min(max(int(limit), 1), 1000)
        with self.lock:
            rows = self.connection.execute("SELECT * FROM audit_entries ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**{k: v for k, v in dict(row).items() if k != "details_json"}, "details": json.loads(row["details_json"])} for row in rows]

    def record_investigation(self, **values):
        values = {
            "id": values.get("id") or str(uuid.uuid4()),
            "started_at": values["started_at"],
            "finished_at": values["finished_at"],
            "namespace": values.get("namespace", ""),
            "status": values.get("status", "success"),
            "total_ms": float(values.get("total_ms", 0)),
            "model_ms": float(values.get("model_ms", 0)),
            "tool_ms": float(values.get("tool_ms", 0)),
            "tool_calls": int(values.get("tool_calls", 0)),
            "input_guardrail_ms": float(values.get("input_guardrail_ms", 0)),
            "output_guardrail_ms": float(values.get("output_guardrail_ms", 0)),
            "mlflow_setup_ms": float(values.get("mlflow_setup_ms", 0)),
            "mlflow_overhead_ms": float(values.get("mlflow_overhead_ms", 0)),
            "mlflow_flush_ms": float(values.get("mlflow_flush_ms", 0)),
            "trace_id": values.get("trace_id", ""),
        }
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT OR REPLACE INTO investigations
                (id, started_at, finished_at, namespace, status, total_ms, model_ms, tool_ms, tool_calls, input_guardrail_ms, output_guardrail_ms, mlflow_setup_ms, mlflow_overhead_ms, mlflow_flush_ms, trace_id)
                VALUES (:id, :started_at, :finished_at, :namespace, :status, :total_ms, :model_ms, :tool_ms, :tool_calls, :input_guardrail_ms, :output_guardrail_ms, :mlflow_setup_ms, :mlflow_overhead_ms, :mlflow_flush_ms, :trace_id)""",
                values,
            )
        return values

    def investigations(self, limit: int = 200) -> list[dict]:
        with self.lock:
            rows = self.connection.execute("SELECT * FROM investigations ORDER BY started_at DESC LIMIT ?", (min(max(limit, 1), 2000),)).fetchall()
        return [dict(row) for row in rows]

    def create_proposal(self, **values) -> dict:
        proposal = {
            "id": values.get("id") or str(uuid.uuid4()),
            "created_at": values.get("created_at") or to_iso(),
            "expires_at": values["expires_at"],
            "fix_id": values["fix_id"],
            "namespace": values["namespace"],
            "resource": values["resource"],
            "status": values.get("status", "pending"),
            "command": values["command"],
            "dry_run_json": stable_json(values.get("dry_run", {})),
            "slack_channel": values.get("slack_channel", ""),
            "slack_ts": values.get("slack_ts", ""),
        }
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT INTO proposals
                (id, created_at, expires_at, fix_id, namespace, resource, status, command, dry_run_json, slack_channel, slack_ts)
                VALUES (:id, :created_at, :expires_at, :fix_id, :namespace, :resource, :status, :command, :dry_run_json, :slack_channel, :slack_ts)""",
                proposal,
            )
        return self.proposal(proposal["id"])

    def proposal(self, proposal_id: str) -> dict | None:
        with self.lock:
            row = self.connection.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["dryRun"] = json.loads(result.pop("dry_run_json"))
        result["result"] = json.loads(result.pop("result_json"))
        return result

    def proposals(self, limit: int = 100) -> list[dict]:
        with self.lock:
            ids = [row[0] for row in self.connection.execute("SELECT id FROM proposals ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 500),)).fetchall()]
        return [item for item in (self.proposal(proposal_id) for proposal_id in ids) if item]

    def active_proposals(self) -> list[dict]:
        with self.lock:
            ids = [row[0] for row in self.connection.execute("SELECT id FROM proposals WHERE status IN ('pending', 'approved', 'executing') ORDER BY created_at DESC").fetchall()]
        return [item for item in (self.proposal(proposal_id) for proposal_id in ids) if item]

    def metric_summary(self, buckets: list[float]) -> dict:
        """Aggregate all persisted history, not a rolling UI page of counter values."""
        with self.lock:
            audit = [dict(row) for row in self.connection.execute("SELECT component, action, outcome, COUNT(*) AS count FROM audit_entries GROUP BY component, action, outcome")]
            runs = [dict(row) for row in self.connection.execute("SELECT status, COUNT(*) AS count FROM investigations GROUP BY status")]
            proposals = [dict(row) for row in self.connection.execute("SELECT status, COUNT(*) AS count FROM proposals GROUP BY status")]
            fixes = [dict(row) for row in self.connection.execute("SELECT fix_id, COUNT(*) AS count FROM proposals WHERE status = 'executed' GROUP BY fix_id")]
            latency = self.connection.execute("SELECT COUNT(*) AS count, COALESCE(SUM(total_ms / 1000.0), 0) AS sum FROM investigations").fetchone()
            histogram = [self.connection.execute("SELECT COUNT(*) FROM investigations WHERE total_ms <= ?", (bucket * 1000,)).fetchone()[0] for bucket in buckets]
        return {"audit": audit, "runs": runs, "proposals": proposals, "fixes": fixes, "latency": dict(latency), "buckets": histogram}

    def update_proposal(self, proposal_id: str, **changes) -> dict | None:
        allowed = {"status", "approved_at", "approval_actor", "executed_at", "slack_channel", "slack_ts"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if "result" in changes:
            values["result_json"] = stable_json(changes["result"])
        if not values:
            return self.proposal(proposal_id)
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.lock, self.connection:
            self.connection.execute(f"UPDATE proposals SET {assignments} WHERE id = ?", (*values.values(), proposal_id))
        return self.proposal(proposal_id)

    def transition_proposal(self, proposal_id: str, expected_status: str, status: str, **changes) -> dict | None:
        """Claim a state transition once; concurrent decisions cannot overwrite one another."""
        allowed = {"approved_at", "approval_actor", "executed_at"}
        values = {key: value for key, value in changes.items() if key in allowed}
        values["status"] = status
        if "result" in changes:
            values["result_json"] = stable_json(changes["result"])
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.lock, self.connection:
            cursor = self.connection.execute(
                f"UPDATE proposals SET {assignments} WHERE id = ? AND status = ?",
                (*values.values(), proposal_id, expected_status),
            )
            if cursor.rowcount != 1:
                return None
        return self.proposal(proposal_id)

    def stats(self) -> dict:
        with self.lock:
            audit = self.connection.execute("SELECT COUNT(*) FROM audit_entries").fetchone()[0]
            runs = self.connection.execute("SELECT COUNT(*) FROM investigations").fetchone()[0]
            pending = self.connection.execute("SELECT COUNT(*) FROM proposals WHERE status = 'pending'").fetchone()[0]
            executed = self.connection.execute("SELECT COUNT(*) FROM proposals WHERE status = 'executed'").fetchone()[0]
        return {"auditEntries": audit, "investigations": runs, "pendingApprovals": pending, "executedFixes": executed}
