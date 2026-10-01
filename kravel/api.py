from __future__ import annotations

import hmac
import json
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


WEB_ROOT = Path(__file__).resolve().parent / "web"
WEB_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/ui/app.css": ("app.css", "text/css; charset=utf-8"),
    "/ui/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/ui/scene.js": ("scene.js", "text/javascript; charset=utf-8"),
    "/ui/topology.mjs": ("topology.mjs", "text/javascript; charset=utf-8"),
    "/ui/demo.mjs": ("demo.mjs", "text/javascript; charset=utf-8"),
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
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-src 'none'; base-uri 'none'; frame-ancestors 'none'")
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
                    return self.send_json(200, {"status": "ready", "mode": "read-only-debugger", "store": store.stats()})
                if path == "/metrics":
                    body = prometheus_metrics(store, "debugger").encode()
                    self.send_response(200); self.send_header("Content-Type", "text/plain; version=0.0.4"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
                if not self.authorized():
                    return self.send_json(401, {"error": "unauthorized"})
                query = self.query()
                namespace = query.get("namespace", config.default_namespace)
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
                    return self.send_json(200, {
                        "agent": {"mode": "read-only", "allowed": ["get", "list", "watch", "pods/log"], "denied": ["secrets", "pods/exec", "create", "update", "patch", "delete"]},
                        "broker": {
                            "namespace": "kravel-demo", "verbs": ["get", "patch"],
                            "resources": {"deployments": ["oom-demo", "image-demo", "crash-demo", "config-demo"], "configmaps": ["config-demo"], "services": ["demo-gateway"]},
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
