from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .karl import grounded_reply, run_guarded_analysis
from .laya import build_classifier_evidence
from .metrics import prometheus_metrics


WEB_ROOT = Path(__file__).resolve().parent / "web"
WEB_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/ui/app.css": ("app.css", "text/css; charset=utf-8"),
    "/ui/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/ui/karl.png": ("karl.png", "image/png"),
}


def create_server(store, config, embedding_client=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Kravel/0.2.0"

        def log_message(self, format, *args):
            return

        def send_json(self, status, payload):
            body = json.dumps(payload, indent=2, ensure_ascii=False).encode()
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
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
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

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path in WEB_ASSETS:
                    return self.send_asset(path)
                if path == "/healthz":
                    return self.send_json(200, {"status": "ok"})
                if path == "/readyz":
                    return self.send_json(200, {"status": "ready", "store": store.stats()})
                if path == "/metrics":
                    body = prometheus_metrics(store, config.cluster_id).encode()
                    self.send_response(200); self.send_header("Content-Type", "text/plain; version=0.0.4"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
                if not self.authorized():
                    return self.send_json(401, {"error": "unauthorized"})
                query = self.query()
                common = {"cluster_id": query.get("clusterId", config.cluster_id)}
                if path == "/v1/state/rewind":
                    return self.send_json(200, store.state_at(**common, timestamp=query.get("timestamp"), namespace=query.get("namespace"), kinds=query.get("kinds", "").split(",") if query.get("kinds") else None, resource_key=query.get("resourceKey")))
                if path == "/v1/state/diff":
                    return self.send_json(200, store.diff_states(**common, from_at=query.get("from"), to_at=query.get("to"), namespace=query.get("namespace"), kinds=query.get("kinds", "").split(",") if query.get("kinds") else None))
                if path == "/v1/state/graph":
                    return self.send_json(200, store.graph_at(**common, timestamp=query.get("timestamp"), namespace=query.get("namespace")))
                if path == "/v1/state/trace":
                    return self.send_json(200, store.trace_resource(**common, timestamp=query.get("timestamp"), resource_key=query.get("resourceKey", ""), max_depth=int(query.get("maxDepth", 2))))
                if path == "/v1/context":
                    embedding = embedding_client.embed(query["question"]) if embedding_client and embedding_client.enabled and query.get("question") else None
                    return self.send_json(200, store.context_shard(**common, incident_at=query.get("incidentAt"), lookback=query.get("lookback", 900), namespace=query.get("namespace", ""), resource_key=query.get("resourceKey", ""), query_embedding=embedding, limit=int(query.get("limit", 100))))
                if path == "/v1/timeline":
                    return self.send_json(200, store.timeline(**common, namespace=query.get("namespace", ""), limit=int(query.get("limit", 500))))
                if path == "/v1/benchmarks":
                    return self.send_json(200, {"runs": store.benchmark_runs(**common, limit=int(query.get("limit", 20)))})
                if path == "/v1/incidents":
                    return self.send_json(200, build_classifier_evidence(store, common["cluster_id"], query.get("baselineAt"), query.get("incidentAt"), query.get("namespace", "")))
                return self.send_json(404, {"error": "not_found"})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                if not self.authorized():
                    return self.send_json(401, {"error": "unauthorized"})
                body = self.body()
                if path == "/v1/ingest/resource":
                    result = store.record_resource_change(cluster_id=body.get("clusterId", config.cluster_id), source=body.get("source", "manual"), action=body.get("action"), object=body.get("object"), observed_at=body.get("observedAt"), event_at=body.get("eventAt"), actor=body.get("actor", ""), audit_id=body.get("auditId", ""))
                    return self.send_json(200 if result["duplicate"] else 201, result)
                if path == "/v1/ingest/audit":
                    events = body.get("items", []) if isinstance(body, dict) and body.get("kind") == "EventList" else body if isinstance(body, list) else [body]
                    results = [store.record_audit_event(config.cluster_id, event) for event in events]
                    return self.send_json(202, {"accepted": len(results), "inserted": sum(item["inserted"] for item in results)})
                if path == "/v1/ingest/kubernetes-event":
                    return self.send_json(202, store.record_kubernetes_event(body.get("clusterId", config.cluster_id), body.get("object", body)))
                if path == "/v1/ingest/metric":
                    return self.send_json(201, store.record_metric_sample(body.get("clusterId", config.cluster_id), body["metricName"], body.get("sampledAt"), body["value"], body.get("labels")))
                if path == "/v1/admin/prune":
                    return self.send_json(200, store.prune(config.retention_days))
                if path == "/v1/karl/chat":
                    return self.send_json(200, grounded_reply(store, config, body))
                if path == "/v1/karl/analyze":
                    if body.get("approved") is not True:
                        return self.send_json(409, {"error": "explicit_approval_required", "message": "Karl needs approval before starting the local deep investigation."})
                    return self.send_json(200, run_guarded_analysis(store, config, body))
                return self.send_json(404, {"error": "not_found"})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})

    return ThreadingHTTPServer((config.host, config.port), Handler)
