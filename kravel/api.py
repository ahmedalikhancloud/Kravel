from __future__ import annotations

import hmac
import json
import re
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .debugger import run_debugger
from .fixes import public_catalog
from .metrics import prometheus_metrics
from .tools import discover_issues, enforce_read_scope, execute_read_tool
from .utils import safe_service_url
from .workflows import WorkflowManager
from .guardrails import public_evidence
from .scenarios import catalog as scenario_catalog, VERSION as SCENARIO_VERSION
from .retrieval import retrieve
from .remediation import load_profiles
from .drafts import repair_mode


WEB_ROOT = Path(__file__).resolve().parent / "web"
WEB_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/ui/app.css": ("app.css", "text/css; charset=utf-8"),
    "/ui/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/ui/scene.js": ("scene.js", "text/javascript; charset=utf-8"),
    "/ui/topology.mjs": ("topology.mjs", "text/javascript; charset=utf-8"),
    "/ui/demo.mjs": ("demo.mjs", "text/javascript; charset=utf-8"),
    "/ui/observability.mjs": ("observability.mjs", "text/javascript; charset=utf-8"),
    "/ui/runbooks.mjs": ("runbooks.mjs", "text/javascript; charset=utf-8"),
    "/ui/vendor/three.module.min.js": ("vendor/three.module.min.js", "text/javascript; charset=utf-8"),
    "/ui/vendor/three.core.min.js": ("vendor/three.core.min.js", "text/javascript; charset=utf-8"),
    "/ui/vendor/OrbitControls.js": ("vendor/OrbitControls.js", "text/javascript; charset=utf-8"),
    "/ui/karl-debugger.png": ("karl-debugger.png", "image/png"),
    "/ui/cursor-default.svg": ("cursor-default.svg", "image/svg+xml"),
    "/ui/cursor-pointer.svg": ("cursor-pointer.svg", "image/svg+xml"),
}


def _broker_request(config, path: str, method: str = "GET", body=None):
    url = safe_service_url(config.broker_url, "approval broker") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json", "User-Agent": "kravel/0.3.0"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method=method), timeout=20) as response:
        return json.load(response)


def _evaluation_request(config, path, method="GET", body=None):
    from .utils import is_internal_hostname
    if not config.evaluator_url:
        raise ValueError("Local evaluator is not installed; run the updated preparation script")
    url = safe_service_url(config.evaluator_url, "local evaluator")
    if not is_internal_hostname(urlparse(url).hostname):
        raise ValueError("Evaluator must remain local")
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(url + path, data=data,
        headers={"Content-Type": "application/json"}, method=method), timeout=10) as response:
        return json.load(response)


def create_server(store, config, kube):
    workflows = WorkflowManager(kube, store, config)
    store.demo_session()
    class Handler(BaseHTTPRequestHandler):
        server_version = "Kravel/0.3.0"

        def log_message(self, _format, *_args):
            return

        def send_json(self, status, payload):
            body = json.dumps(public_evidence(payload), indent=2, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_asset(self, path):
            filename, content_type = WEB_ASSETS[path]
            body = (WEB_ROOT / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-src http://127.0.0.1:8082; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            return not config.api_token or hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {config.api_token}")

        def query(self):
            return {key: values[-1] for key, values in parse_qs(urlparse(self.path).query).items()}

        def body(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length > config.max_body_bytes:
                raise ValueError("request body is too large")
            return json.loads(self.rfile.read(length) or b"{}")

        def read_tool(self, name, raw_args, namespace):
            started = time.perf_counter()
            args, outcome = {}, "error"
            try:
                args = enforce_read_scope(name, raw_args, namespace)
                result = execute_read_tool(name, args, kube)
                outcome = "success"
                return {"tool": name, "arguments": args, "result": result, "durationMs": (time.perf_counter() - started) * 1000}
            finally:
                store.record("debugger", f"tool.{name}", actor="operator", resource=str(args.get("name") or args.get("pod") or args.get("kind") or ""), outcome=outcome, duration_ms=(time.perf_counter() - started) * 1000, details={"namespace": namespace})

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path in WEB_ASSETS:
                    return self.send_asset(path)
                if path == "/healthz":
                    return self.send_json(200, {"status": "ok"})
                if path == "/readyz":
                    return self.send_json(200, {"status": "ready", "mode": "approval-gated-repair-agent", "store": store.stats()})
                if path == "/metrics":
                    body = prometheus_metrics(store, "debugger").encode()
                    self.send_response(200); self.send_header("Content-Type", "text/plain; version=0.0.4"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
                if not self.authorized():
                    return self.send_json(401, {"error": "unauthorized"})
                query = self.query()
                if path == "/v1/evaluations/catalog":
                    return self.send_json(200, _evaluation_request(config, "/v1/catalog"))
                if path == "/v1/evaluations":
                    return self.send_json(200, _evaluation_request(config, "/v1/jobs?runId=" + urllib.parse.quote(query.get("runId", ""), safe="")))
                if path.startswith("/v1/evaluations/"):
                    job_id = path.split("/")[-1]
                    if not re.fullmatch(r"[a-f0-9-]{36}", job_id):
                        raise ValueError("Invalid evaluation job ID")
                    return self.send_json(200, _evaluation_request(config, "/v1/jobs/" + job_id))
                namespace = query.get("namespace", config.default_namespace)
                if path == "/v1/runbooks":
                    cases = scenario_catalog()
                    return self.send_json(200, {"scenarios": cases, "version": SCENARIO_VERSION,
                        "counts": {group: sum(c["group"] == group for c in cases) for group in ("common", "difficult")},
                        "notice": "Curated coverage, not a universal production frequency ranking. Knowledge never grants mutation permissions."})
                if path == "/v1/runbooks/search":
                    return self.send_json(200, retrieve(query.get("q", ""), limit=6))
                if path == "/v1/demo-session":
                    return self.send_json(200, {"session": store.demo_session(), "historyPreserved": True})
                if path == "/v1/cluster":
                    result = discover_issues(kube, namespace)
                    store.record("debugger", "cluster.inspected", actor="operator", resource=namespace, duration_ms=result["durationMs"], details={"podCount": result["podCount"], "issueCount": len(result["issues"])})
                    return self.send_json(200, result)
                if path == "/v1/resource":
                    return self.send_json(200, self.read_tool("get_resource", {"kind": query["kind"], "name": query["name"], "namespace": namespace}, namespace)["result"])
                if path == "/v1/logs":
                    return self.send_json(200, self.read_tool("pod_logs", {"pod": query["pod"], "namespace": namespace, "container": query.get("container", ""), "previous": query.get("previous", "false").lower() == "true", "tail_lines": query.get("tailLines", 120)}, namespace)["result"])
                if path == "/v1/events":
                    return self.send_json(200, self.read_tool("get_events", {"namespace": namespace, "regarding_name": query.get("regardingName", ""), "limit": query.get("limit", 100)}, namespace)["result"])
                if path == "/v1/catalog":
                    return self.send_json(200, {"fixes": public_catalog(), "policy": {"namespace": "kravel-demo", "approvalTimeoutSeconds": 300, "arbitraryCommands": False}})
                if path == "/v1/proposals":
                    payload = _broker_request(config, "/v1/proposals")
                    payload["proposals"] = workflows.observe_proposals(payload.get("proposals", []))
                    return self.send_json(200, payload)
                if path == "/v1/investigations":
                    return self.send_json(200, {"runs": [{k: v for k, v in run.items() if k not in {"payload", "steps"}} for run in store.workflows()]})
                if path == "/v1/activity":
                    return self.send_json(200, {"investigationActive": workflows.model_slot.locked(), "verificationActive": any(run["status"] == "running" for run in store.workflows("verification", 100))})
                if path.startswith("/v1/investigations/"):
                    run = store.workflow(path.split("/")[-1])
                    return self.send_json(200 if run and run["kind"] == "investigation" else 404, run if run and run["kind"] == "investigation" else {"error": "not_found"})
                if path == "/v1/audit":
                    local = store.audit_entries(int(query.get("limit", 200)))
                    try:
                        broker = _broker_request(config, "/v1/audit").get("entries", [])
                    except Exception:
                        broker = []
                    entries = sorted([*local, *broker], key=lambda item: item.get("at", ""), reverse=True)[:300]
                    unavailable = []
                    if getattr(config, "operator_url", ""):
                        try:
                            with urllib.request.urlopen(safe_service_url(config.operator_url, "operator audit") + "/v1/audit", timeout=3) as response:
                                entries.extend(json.load(response).get("entries", []))
                        except Exception:
                            unavailable.append("operator audit")
                    return self.send_json(200, {"entries": sorted(entries, key=lambda item: item.get("at", ""), reverse=True)[:300], "unavailable": unavailable})
                if path == "/v1/capabilities":
                    enrolled = load_profiles()
                    return self.send_json(200, {
                        "agent": {"mode": "approval-gated-repair", "allowed": ["get", "list", "watch", "pods/log", "draft_repair", "request_repair_approval"], "writesVia": "approval-broker", "directKubernetesWrites": False, "denied": ["secrets", "pods/exec", "create", "update", "delete", "approve", "rbac"]},
                        "broker": {
                            "namespace": "kravel-demo", "verbs": ["get", "patch"],
                            "resources": {"deployments": "existing resources in kravel-demo", "daemonsets": "existing resources in kravel-demo", "configmaps": "existing resources in kravel-demo", "services": "existing resources in kravel-demo"},
                            "repairMode": repair_mode(),
                            "enrolledProfiles": [{k: p[k] for k in ("id", "kind", "name", "scenarioId")} for p in enrolled],
                            "novelRepairs": "Validated structured patches + server dry-run + human approval; no per-resource enrollment in approval_gated mode",
                            "requiresHumanApproval": True, "approvalTimeoutSeconds": 300,
                        },
                        "console": {"mode": "human-only", "namespace": "kravel-demo", "shell": False, "agentAccess": False, "writesRequirePreviewConfirmation": True},
                    })
                return self.send_json(404, {"error": "not_found"})
            except Exception as exc:
                return self.send_json(400, {"error": str(exc)})

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                if not self.authorized():
                    return self.send_json(401, {"error": "unauthorized"})
                body = self.body()
                if path == "/v1/evaluations":
                    if not isinstance(body, dict) or set(body) - {"runId", "profile", "expectedFacts", "expectedResponse"}:
                        raise ValueError("Only a completed run, profile, and independent references may be evaluated")
                    run = store.workflow(str(body.get("runId", "")))
                    if not run or run["kind"] != "investigation" or run["status"] != "completed" or not run["payload"].get("traceId"):
                        raise ValueError("Choose a completed investigation with a recorded MLflow trace")
                    return self.send_json(202, _evaluation_request(config, "/v1/jobs", "POST", {**body, "traceId": run["payload"]["traceId"]}))
                namespace = str(body.get("namespace") or config.default_namespace)
                if path == "/v1/demo-session":
                    if not workflows.model_slot.acquire(blocking=False):
                        return self.send_json(409, {"error": "Finish the current investigation before starting a fresh view."})
                    try:
                        proposals = _broker_request(config, "/v1/proposals").get("proposals", [])
                        if any(p["status"] in {"pending", "approved", "executing"} for p in proposals) or any(r["status"] == "running" for r in store.workflows("verification", 100)):
                            return self.send_json(409, {"error": "Resolve pending approvals and recovery checks first. Active work cannot be hidden."})
                        session = store.start_demo_session()
                        store.record("debugger", "demo.view_started", actor="operator", details={"sessionId": session["id"], "historyPreserved": True})
                        return self.send_json(200, {"session": session, "historyPreserved": True, "clusterReset": False})
                    finally:
                        workflows.model_slot.release()
                if path == "/v1/investigations":
                    question = str(body.get("message") or "").strip()
                    if not question:
                        raise ValueError("message is required")
                    try:
                        return self.send_json(202, workflows.start(question, namespace, str(body.get("target") or "")[:250]))
                    except RuntimeError as exc:
                        return self.send_json(409, {"error": str(exc)})
                if path == "/v1/chat":
                    question = str(body.get("message") or "").strip()
                    if not question:
                        raise ValueError("message is required")
                    if not workflows.model_slot.acquire(blocking=False):
                        return self.send_json(409, {"error": "Karl is already investigating"})
                    try:
                        return self.send_json(200, run_debugger(kube, store, config, question, namespace))
                    finally:
                        workflows.model_slot.release()
                if path == "/v1/tools/run":
                    name = str(body.get("tool") or "")
                    return self.send_json(200, self.read_tool(name, body.get("arguments") or {}, namespace))
                if path == "/v1/proposals":
                    if "draftId" in body:
                        run = store.workflow(str(body.get("runId", "")))
                        if not run or run["kind"] != "investigation" or run["status"] != "completed" or run["payload"].get("disposition") == "blocked":
                            raise ValueError("Choose a completed, permitted investigation with an evidence-linked draft")
                        draft = next((d for d in run["payload"].get("draftRepairs", []) if d["id"] == body["draftId"]), None)
                        if not draft:
                            raise ValueError("Draft was not recorded by this investigation")
                        proposal = _broker_request(config, "/v1/proposals", "POST", {"draft": draft["draft"], "namespace": run["namespace"], "actor": "human-requested-draft-review"})
                        store.record("debugger", "draft.forwarded", actor="operator", resource=proposal.get("resource", ""), details={"proposalId": proposal.get("id", ""), "runId": run["id"]})
                        return self.send_json(201, proposal)
                    payload = {"fixId": str(body.get("fixId") or ""), "namespace": namespace, "actor": "kravel-debugger"}
                    proposal = _broker_request(config, "/v1/proposals", "POST", payload)
                    store.record("debugger", "proposal.forwarded", actor="operator", resource=proposal.get("resource", ""), outcome=proposal.get("status", "pending"), details={"proposalId": proposal.get("id", ""), "fixId": payload["fixId"]})
                    return self.send_json(201, proposal)
                return self.send_json(404, {"error": "not_found"})
            except Exception as exc:
                return self.send_json(400, {"error": str(exc)})

    server = ThreadingHTTPServer((config.host, config.port), Handler)
    server.workflows = workflows
    return server
