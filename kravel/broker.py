from __future__ import annotations

import hmac
import copy
import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .fixes import get_fix, summarize_result
from .metrics import prometheus_metrics
from .evidence import Progress
from .tracing import MlflowTracer
from .slack import SlackApprovalClient
from .utils import stable_json, to_iso
from .drafts import draft_fix, resolve_proposal


WEB_ROOT = Path(__file__).resolve().parent / "web"
ASSETS = {
    "/": ("slack.html", "text/html; charset=utf-8"),
    "/index.html": ("slack.html", "text/html; charset=utf-8"),
    "/ui/slack.css": ("slack.css", "text/css; charset=utf-8"),
    "/ui/slack.js": ("slack.js", "text/javascript; charset=utf-8"),
    "/ui/karl-debugger.png": ("karl-debugger.png", "image/png"),
    "/ui/cursor-default.svg": ("cursor-default.svg", "image/svg+xml"),
    "/ui/cursor-pointer.svg": ("cursor-pointer.svg", "image/svg+xml"),
}


class ApprovalBroker:
    def __init__(self, kube, store, config):
        self.kube, self.store, self.config = kube, store, config
        self.slack = SlackApprovalClient(config.slack_bot_token, config.slack_channel_id)
        self.threads: dict[str, threading.Thread] = {}
        self.lock = threading.RLock()
        store.demo_session()
        for proposal in self.store.active_proposals():
            if proposal["status"] == "pending":
                self._start_waiter(proposal["id"])
            elif proposal["status"] in {"approved", "executing"}:
                # An interrupted execution may have partially succeeded. Never replay it.
                self.store.transition_proposal(proposal["id"], proposal["status"], "failed", result={"error": "Broker restarted during approval/execution; inspect and prepare a new proposal."})
                self.store.record("approval-broker", "proposal.interrupted", resource=proposal["resource"], outcome="error", details={"proposalId": proposal["id"]})
                self.store.update_workflow(proposal["id"], status="interrupted")

    def _tracer(self):
        return MlflowTracer(getattr(self.config, "mlflow_url", ""), getattr(self.config, "mlflow_experiment", "Kravel Guarded Debugger"))

    def _start_waiter(self, proposal_id: str):
        thread = threading.Thread(target=self._wait, args=(proposal_id,), daemon=True, name=f"approval-{proposal_id[:8]}")
        self.threads[proposal_id] = thread
        thread.start()

    @staticmethod
    def _fingerprint(obj: dict) -> str:
        return hashlib.sha256(stable_json({key: obj.get(key) for key in ("spec", "data", "binaryData")}).encode()).hexdigest()

    @staticmethod
    def _plan_hash(fix: dict) -> str:
        return hashlib.sha256(stable_json(fix["operations"]).encode()).hexdigest()

    def _guarded_patch(self, operation: dict, obj: dict) -> dict:
        patch = copy.deepcopy(operation["patch"])
        metadata = obj.get("metadata", {})
        if not metadata.get("uid") or not metadata.get("resourceVersion"):
            raise ValueError("Resource identity/version is missing; refusing mutation")
        containers = patch.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        existing = {c.get("name") for c in obj.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])}
        if any(c.get("name") not in existing for c in containers):
            raise ValueError("Repair may not add an unreviewed container to this workload")
        patch.setdefault("metadata", {}).update({"uid": metadata["uid"], "resourceVersion": metadata["resourceVersion"]})
        return patch

    def _dry_run(self, fix: dict) -> list[dict]:
        results = []
        for operation in fix["operations"]:
            obj = self.kube.get_resource(operation["kind"], operation["name"], fix["namespace"])["object"]
            response = self.kube.patch(operation["kind"], operation["name"], fix["namespace"], self._guarded_patch(operation, obj), content_type=operation["contentType"], dry_run=True)
            results.append({**summarize_result(operation, response), "beforeUid": obj["metadata"]["uid"], "beforeFingerprint": self._fingerprint(obj), "planHash": self._plan_hash(fix)})
            if fix.get("draft"):
                results[-1]["reviewedDraft"] = copy.deepcopy(fix["draft"])
        return results

    def create(self, fix_id: str, namespace: str, actor: str = "kravel-debugger") -> dict:
        with self.lock:
            return self._create(fix_id, namespace, actor)

    def create_draft(self, draft: dict, namespace: str, actor: str = "kravel-debugger") -> dict:
        with self.lock:
            fix = draft_fix(draft, namespace)  # Revalidate deterministic authority at the broker.
            return self._create(fix["id"], namespace, actor, prepared_fix=fix)

    def _create(self, fix_id: str, namespace: str, actor: str, prepared_fix=None) -> dict:
        for existing in self.store.active_proposals():
            if existing["fix_id"] == fix_id and existing["namespace"] == namespace and existing["status"] in {"pending", "approved", "executing"}:
                return existing
        proposal_id = str(uuid.uuid4())
        fix = prepared_fix or get_fix(fix_id, namespace, proposal_id)
        self.store.start_workflow(proposal_id, "repair", namespace, fix["resource"])
        tracer = self._tracer()
        self.store.workflow_step(proposal_id, "review_trace_setup", "Initialize review tracing", "completed", duration_ms=tracer.setup_ms)
        progress = Progress(self.store, proposal_id, tracer)
        dry_started = time.perf_counter()
        try:
            with tracer.span("repair.review", "CHAIN", {"proposal_id": proposal_id}), progress.step("dry_run", "Kubernetes server dry-run"):
                dry_run = self._dry_run(fix)
        except Exception:
            self.store.update_workflow(proposal_id, status="failed")
            raise
        finally:
            tracer.flush()
        dry_ms = (time.perf_counter() - dry_started) * 1000
        proposal = self.store.create_proposal(
            id=proposal_id,
            fix_id=fix_id,
            namespace=namespace,
            resource=fix["resource"],
            command=fix["command"],
            dry_run=dry_run,
            expires_at=to_iso(datetime.now(timezone.utc) + timedelta(seconds=self.config.approval_timeout_seconds)),
        )
        self.store.record("approval-broker", "proposal.created", actor=actor, resource=fix["resource"], outcome="pending", duration_ms=dry_ms, details={"proposalId": proposal["id"], "fixId": fix_id, "dryRun": "passed", "timeoutSeconds": self.config.approval_timeout_seconds})
        if self.slack.enabled:
            try:
                message = self.slack.post(proposal)
                proposal = self.store.update_proposal(proposal["id"], slack_channel=message["channel"], slack_ts=message["ts"])
                self.store.record("approval-broker", "slack.posted", actor="broker", resource=fix["resource"], details={"proposalId": proposal["id"], "channel": message["channel"]})
            except Exception as exc:
                self.store.record("approval-broker", "slack.failed", actor="broker", resource=fix["resource"], outcome="error", details={"proposalId": proposal["id"], "errorType": type(exc).__name__})
        self.store.workflow_step(proposal_id, "approval", "Human approval · 5-minute deadline", "running")
        self._start_waiter(proposal["id"])
        return proposal

    def _decision_progress(self, proposal, status):
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(proposal["created_at"].replace("Z", "+00:00"))).total_seconds()*1000
        self.store.workflow_step(proposal["id"], "approval", "Human approval · 5-minute deadline", status, duration_ms=elapsed, details={"actor": proposal.get("approval_actor", ""), "deadline": proposal["expires_at"]})

    def _wait(self, proposal_id: str):
        last_slack_poll = 0.0
        while True:
            proposal = self.store.proposal(proposal_id)
            if not proposal:
                return
            if proposal["status"] == "approved":
                return self._execute(proposal)
            if proposal["status"] in {"rejected", "expired", "executing", "executed", "failed"}:
                return
            expires = datetime.fromisoformat(proposal["expires_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) >= expires:
                self._expire(proposal)
                return
            if self.slack.enabled and proposal.get("slack_ts") and time.monotonic() - last_slack_poll >= 3:
                last_slack_poll = time.monotonic()
                try:
                    decision = self.slack.decision(proposal["slack_channel"], proposal["slack_ts"])
                    if decision:
                        status, actor = decision
                        if status == "approved":
                            self.approve(proposal_id, f"slack:{actor}")
                        else:
                            self.reject(proposal_id, f"slack:{actor}")
                except Exception as exc:
                    self.store.record("approval-broker", "slack.poll_failed", actor="broker", resource=proposal["resource"], outcome="error", details={"proposalId": proposal_id, "errorType": type(exc).__name__})
            time.sleep(1)

    def _expire(self, proposal: dict):
        updated = self.store.transition_proposal(proposal["id"], "pending", "expired")
        if updated:
            self._decision_progress(updated, "expired")
            self.store.update_workflow(proposal["id"], status="expired")
            self.store.record("approval-broker", "proposal.expired", actor="broker", resource=proposal["resource"], outcome="timeout", details={"proposalId": proposal["id"]})
        return updated or self.store.proposal(proposal["id"])

    def approve(self, proposal_id: str, actor: str) -> dict:
        proposal = self.store.proposal(proposal_id)
        if not proposal:
            raise ValueError("Proposal not found")
        if proposal["status"] != "pending":
            return proposal
        expires = datetime.fromisoformat(proposal["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) >= expires:
            return self._expire(proposal)
        updated = self.store.transition_proposal(proposal_id, "pending", "approved", approved_at=to_iso(), approval_actor=actor)
        if updated:
            self._decision_progress(updated, "completed")
            self.store.record("approval-broker", "proposal.approved", actor=actor, resource=proposal["resource"], outcome="approved", details={"proposalId": proposal_id})
        return updated or self.store.proposal(proposal_id)

    def reject(self, proposal_id: str, actor: str) -> dict:
        proposal = self.store.proposal(proposal_id)
        if not proposal:
            raise ValueError("Proposal not found")
        if proposal["status"] == "pending":
            updated = self.store.transition_proposal(proposal_id, "pending", "rejected", approval_actor=actor)
            if updated:
                self._decision_progress(updated, "rejected")
                self.store.update_workflow(proposal_id, status="rejected")
                self.store.record("approval-broker", "proposal.rejected", actor=actor, resource=proposal["resource"], outcome="rejected", details={"proposalId": proposal_id})
        return self.store.proposal(proposal_id)

    def _execute(self, proposal: dict):
        proposal = self.store.transition_proposal(proposal["id"], "approved", "executing")
        if not proposal:
            return None
        started = time.perf_counter()
        results = []
        tracer = self._tracer()
        progress = Progress(self.store, proposal["id"], tracer)
        self.store.workflow_step(proposal["id"], "execution_trace_setup", "Initialize execution tracing", "completed", duration_ms=tracer.setup_ms)
        root_context = tracer.span("repair.workflow", "CHAIN", {"proposal_id": proposal["id"], "fix_id": proposal["fix_id"]})
        root_span = root_context.__enter__()
        try:
            fix = resolve_proposal(proposal)
            dry_runs = proposal["dryRun"]
            if len(dry_runs) != len(fix["operations"]) or any(result.get("planHash") != self._plan_hash(fix) for result in dry_runs):
                raise ValueError("Fix catalog changed since review; a new dry-run and approval are required")
            prepared = []
            revalidation_started = time.perf_counter()
            self.store.workflow_step(proposal["id"], "revalidate", "Revalidate reviewed identities & specification", "running")
            for operation, reviewed in zip(fix["operations"], dry_runs):
                with tracer.span("repair.revalidate_resource", "TOOL", {"resource": operation["name"]}):
                    obj = self.kube.get_resource(operation["kind"], operation["name"], fix["namespace"])["object"]
                if obj.get("metadata", {}).get("uid") != reviewed.get("beforeUid") or self._fingerprint(obj) != reviewed.get("beforeFingerprint"):
                    raise ValueError("Resource changed or was reset after dry-run; a new proposal is required")
                prepared.append((operation, self._guarded_patch(operation, obj)))
            self.store.workflow_step(proposal["id"], "revalidate", "Revalidate reviewed identities & specification", "completed", duration_ms=(time.perf_counter()-revalidation_started)*1000)
            with tracer.span("repair.apply", "CHAIN", {"proposal_id": proposal["id"], "approval_wait_ms": (datetime.fromisoformat(proposal["approved_at"].replace("Z", "+00:00"))-datetime.fromisoformat(proposal["created_at"].replace("Z", "+00:00"))).total_seconds()*1000}) as root:
                for index, (operation, patch) in enumerate(prepared, 1):
                    resource = f"{operation['kind']}/{operation['name']}"
                    with progress.step(f"apply_{index}", f"Apply operation {index} · {resource}", {"resource": resource}), tracer.span("repair.operation", "TOOL", {"operation": index, "resource": resource}):
                        response = self.kube.patch(operation["kind"], operation["name"], fix["namespace"], patch, content_type=operation["contentType"], dry_run=False)
                        results.append(summarize_result(operation, response))
                    self.store.record("approval-broker", "fix.operation", actor=proposal["approval_actor"], resource=resource, duration_ms=response.get("durationMs", 0), trace_id=tracer.trace_id, details={"proposalId": proposal["id"], "operation": index})
                root.set_outputs({"status": "patch_accepted", "operation_count": len(results)})
            elapsed = (time.perf_counter() - started) * 1000
            updated = self.store.update_proposal(proposal["id"], status="executed", executed_at=to_iso(), result={"operations": results})
            self.store.update_workflow(proposal["id"], status="patch_accepted", payload={"traceId": tracer.trace_id, "operations": results, "note": "API accepted the patches; independent read-only recovery verification is still required."})
            self.store.record("approval-broker", "fix.executed", actor=proposal["approval_actor"], resource=proposal["resource"], outcome="success", duration_ms=elapsed, details={"proposalId": proposal["id"], "fixId": proposal["fix_id"]})
            if self.slack.enabled and proposal.get("slack_ts"):
                try:
                    self.slack.update(proposal["slack_channel"], proposal["slack_ts"], f"✅ Kravel executed approved fix {proposal['fix_id']} for {proposal['resource']}. Audit ID: {proposal['id'][:8]}")
                except Exception:
                    pass
            return updated
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000
            self.store.update_proposal(proposal["id"], status="failed", executed_at=to_iso(), result={"error": str(exc), "operations": results})
            self.store.update_workflow(proposal["id"], status="partially_failed" if results else "failed", payload={"error": str(exc), "operations": results})
            for step in (self.store.workflow(proposal["id"]) or {}).get("steps", []):
                if step["status"] == "running":
                    self.store.workflow_step(proposal["id"], step["step_key"], step["label"], "failed", details={"error": str(exc)})
            self.store.record("approval-broker", "fix.failed", actor=proposal["approval_actor"], resource=proposal["resource"], outcome="error", duration_ms=elapsed, details={"proposalId": proposal["id"], "fixId": proposal["fix_id"], "errorType": type(exc).__name__})
        finally:
            root_span.set_outputs({"accepted_operation_count": len(results), "status": self.store.proposal(proposal["id"])["status"]})
            root_context.__exit__(None, None, None)
            flush_ms = tracer.flush()
            self.store.workflow_step(proposal["id"], "execution_trace_export", "Export execution trace", "completed", duration_ms=flush_ms)


def create_broker_server(broker: ApprovalBroker, config):
    class Handler(BaseHTTPRequestHandler):
        server_version = "KravelApprovalBroker/0.3.0"

        def log_message(self, _format, *_args):
            return

        def json(self, status: int, payload):
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length > config.max_body_bytes:
                raise ValueError("request body is too large")
            return json.loads(self.rfile.read(length) or b"{}")

        def send_asset(self, path):
            filename, content_type = ASSETS[path]
            body = (WEB_ROOT / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def local_authorized(self):
            supplied = self.headers.get("X-Kravel-Approval-Token", "")
            return bool(config.approval_token) and hmac.compare_digest(supplied, config.approval_token)

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path in ASSETS:
                    return self.send_asset(path)
                if path in {"/healthz", "/readyz"}:
                    return self.json(200, {"status": "ready", "mode": "slack+local" if broker.slack.enabled else "local", "store": broker.store.stats()})
                if path == "/metrics":
                    body = prometheus_metrics(broker.store, "approval-broker").encode()
                    self.send_response(200); self.send_header("Content-Type", "text/plain; version=0.0.4"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
                if path == "/v1/proposals":
                    return self.json(200, {"proposals": [{**p, "workflow": broker.store.workflow(p["id"])} for p in broker.store.proposals(100)], "session": broker.store.demo_session(), "timeoutSeconds": config.approval_timeout_seconds, "slackEnabled": broker.slack.enabled})
                if path.startswith("/v1/proposals/"):
                    proposal = broker.store.proposal(path.split("/")[3])
                    return self.json(200 if proposal else 404, proposal or {"error": "not_found"})
                if path == "/v1/audit":
                    return self.json(200, {"entries": broker.store.audit_entries(300)})
                return self.json(404, {"error": "not_found"})
            except Exception as exc:
                return self.json(400, {"error": str(exc)})

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                body = self.body()
                if path == "/v1/demo-session":
                    if not self.local_authorized():
                        return self.json(401, {"error": "approval_token_required"})
                    with broker.lock:
                        if broker.store.active_proposals():
                            return self.json(409, {"error": "Active approvals cannot be hidden. Resolve them first."})
                        session = broker.store.start_demo_session()
                        broker.store.record("approval-broker", "demo.view_started", actor="local-human", details={"sessionId": session["id"], "historyPreserved": True})
                        return self.json(200, {"session": session, "historyPreserved": True})
                if path == "/v1/proposals":
                    if "draft" in body:
                        return self.json(201, broker.create_draft(body["draft"], str(body.get("namespace") or config.default_namespace), str(body.get("actor") or "kravel-debugger")))
                    return self.json(201, broker.create(str(body.get("fixId", "")), str(body.get("namespace") or config.default_namespace), str(body.get("actor") or "kravel-debugger")))
                if path.startswith("/v1/proposals/") and path.endswith(("/approve", "/reject")):
                    if not self.local_authorized():
                        broker.store.record("approval-broker", "approval.denied", actor="unauthenticated", outcome="denied", details={"reason": "invalid_or_missing_token"})
                        return self.json(401, {"error": "approval_token_required"})
                    proposal_id, action = path.split("/")[3:5]
                    actor = str(body.get("actor") or "local-human")[:80]
                    proposal = broker.approve(proposal_id, actor) if action == "approve" else broker.reject(proposal_id, actor)
                    return self.json(200, proposal)
                return self.json(404, {"error": "not_found"})
            except Exception as exc:
                broker.store.record("approval-broker", "request.rejected", actor="caller", outcome="error", details={"path": path, "errorType": type(exc).__name__})
                return self.json(400, {"error": str(exc)})

    return ThreadingHTTPServer((config.host, config.port), Handler)
