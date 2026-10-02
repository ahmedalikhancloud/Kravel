"""Fixed local inference transport. Captures actual judge requests without auth headers.

MLflow's OpenAI-compatible gateway uses this loopback endpoint, never a cloud fallback.
No kubectl, shell, network tools, or approval credentials are exposed to judges.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .tracing import trace_content


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError("Local judge redirects are forbidden")


class JudgeRequestRejected(ValueError):
    """Fixed policy codes only; never include user content or upstream errors."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def local_model_url(value):
    parsed = urlparse(value)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in
            {"localhost", "127.0.0.1", "::1", "host.docker.internal", "model-runner.docker.internal"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Judge endpoint must be a credential-free local Model Runner endpoint")
    return value.rstrip("/")


class JudgeBridge:
    def __init__(self, model, base_url, timeout=45):
        self.model, self.base_url = model, local_model_url(base_url)
        self.timeout = timeout
        self.opener = urllib.request.build_opener(_NoRedirect())
        self.active = None
        self.lock = threading.Lock()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def send(self, code, body):
                encoded = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_POST(self):
                span = None
                active = None
                started = time.perf_counter()
                try:
                    if self.path != "/v1/chat/completions":
                        raise JudgeRequestRejected("endpoint_policy")
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 120_000:
                        raise JudgeRequestRejected("request_body_budget")
                    body = json.loads(self.rfile.read(length))
                    with bridge.lock:
                        active = bridge.active
                        if not active or active["calls"] >= 3 or time.monotonic() > active["deadline"]:
                            raise JudgeRequestRejected("call_budget_or_inactive")
                        active["calls"] += 1
                    if body.get("model") != bridge.model or body.get("stream") or not isinstance(body.get("messages"), list):
                        raise JudgeRequestRejected("model_request_policy")
                    if len(json.dumps(body["messages"])) > 48_000:
                        raise JudgeRequestRejected("context_budget")
                    body["temperature"] = 0
                    body.pop("max_completion_tokens", None)
                    body["max_tokens"] = 800
                    # Native trace-inspection tools may occur. They can only inspect the
                    # supplied MLflow trace; this process has no Kubernetes client/identity.
                    import mlflow
                    span = mlflow.start_span_no_context("judge.local_inference", "LLM", parent_span=active["parent"])
                    span.set_inputs(trace_content(body, 192_000, 48_000, quarantine_instructions=False))
                    span.set_attribute("mlflow.chat.model", bridge.model)
                    span.set_attribute("mlflow.chat.provider", "local-openai-compatible")
                    span.set_attribute("kravel.judge_call", active["calls"])
                    request = urllib.request.Request(bridge.base_url + "/chat/completions",
                        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
                    with bridge.lock:
                        active["inferenceCalls"] += 1
                    with bridge.opener.open(request, timeout=bridge.timeout) as response:
                        raw = response.read(2_000_001)
                    if len(raw) > 2_000_000:
                        raise ValueError("Judge response exceeds transport budget")
                    result = json.loads(raw)
                    usage = result.get("usage", {})
                    span.set_attribute("mlflow.chat.tokenUsage", {
                        "input_tokens": int(usage.get("prompt_tokens") or 0),
                        "output_tokens": int(usage.get("completion_tokens") or 0),
                        "total_tokens": int(usage.get("total_tokens") or 0)})
                    span.end(outputs=trace_content(result, 192_000, 48_000), attributes={"kravel.latency_ms": (time.perf_counter()-started)*1000})
                    span = None
                    return self.send(200, result)
                except Exception as exc:
                    if active:
                        active["errors"].append(type(exc).__name__)
                        if isinstance(exc, JudgeRequestRejected):
                            active["rejections"].append(exc.code)
                    # Do not store upstream bodies, URLs, raw exceptions or stack locals.
                    if span:
                        span.end(outputs={"error_type": type(exc).__name__, "note": "Local judge inference failed; no score was fabricated."}, status="ERROR")
                    elif active:
                        import mlflow
                        rejected = mlflow.start_span_no_context("judge.transport_rejected", "GUARDRAIL", parent_span=active["parent"])
                        rejected.end(outputs={"decision": "deny", "error_type": type(exc).__name__,
                            "reason_code": exc.code if isinstance(exc, JudgeRequestRejected) else "invalid_request",
                            "model_invoked": False, "note": "Fixed local model, request budget or transport check failed; no cloud fallback."}, status="ERROR")
                    return self.send(400, {"error": {"message": "Local judge call failed: " + type(exc).__name__, "type": "local_judge_error"}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True, name="judge-loopback").start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    @contextmanager
    def activate(self, parent):
        with self.lock:
            if self.active:
                raise RuntimeError("Only one scorer can invoke the judge at a time")
            context = {"parent": parent, "calls": 0, "inferenceCalls": 0, "errors": [], "rejections": [], "deadline": time.monotonic()+150}
            self.active = context
        try:
            yield context
        finally:
            with self.lock:
                self.active = None
