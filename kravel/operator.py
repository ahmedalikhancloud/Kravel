"""Human-only kubectl-compatible console; never a host shell or agent tool.

Commands are parsed into bounded Kubernetes API calls. No subprocess, PTY,
host kubeconfig, Docker socket, shell expansion, plugins, or arbitrary patches.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import secrets
import shlex
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .guardrails import public_evidence
from .metrics import prometheus_metrics
from .utils import stable_json, to_iso


NAMESPACE = "kravel-demo"
ORIGIN = "http://127.0.0.1:8082"
DEPLOYMENTS = {"oom-demo", "image-demo", "crash-demo", "config-demo", "net-demo"}
KINDS = {"pod": "pods", "pods": "pods", "po": "pods", "deployment": "deployments", "deployments": "deployments", "deploy": "deployments", "replicaset": "replicasets", "replicasets": "replicasets", "rs": "replicasets", "configmap": "configmaps", "configmaps": "configmaps", "cm": "configmaps", "service": "services", "services": "services", "svc": "services", "events": "events", "event": "events", "endpointslices": "endpointslices"}
HEALTHY_ARGS = ["exec sleep 86400"]
CRASH_ARGS = ["echo FATAL: simulated startup dependency failure >&2; exit 42"]
OOM_ARGS = ['BEGIN { for (i = 0; i < 96; i++) blocks[i] = sprintf("%1048576s", "x"); while (1) system("sleep 3600") }']


def _name(value):
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", value):
        raise ValueError("Invalid Kubernetes name")
    return value


def _target(tokens):
    if not tokens:
        raise ValueError("Resource kind required")
    first = tokens.pop(0).split("/")
    kind = KINDS.get(first[0])
    if not kind or len(first) > 2:
        raise ValueError("Unsupported resource; Secrets and cluster-scoped resources are denied")
    name = first[1] if len(first) == 2 else tokens.pop(0) if tokens and not tokens[0].startswith("-") else ""
    return kind, _name(name) if name else ""


def _flags(tokens, allowed):
    flags = {}
    while tokens:
        item = tokens.pop(0)
        key, sep, value = item.partition("=")
        if key not in allowed or key in flags:
            raise ValueError(f"Unsupported/duplicate option: {key}")
        if allowed[key] == "bool":
            if sep:
                raise ValueError("Boolean flag takes no value")
            flags[key] = True
        else:
            if not sep:
                if not tokens or tokens[0].startswith("-"):
                    raise ValueError(f"Value required for {key}")
                value = tokens.pop(0)
            flags[key] = value
    return flags


def validate_patch(kind, name, patch):
    if not isinstance(patch, dict) or not patch:
        raise ValueError("Nonempty object patch required")
    if kind == "configmaps" and name == "config-demo":
        if patch not in ({"data": {"MODE": "healthy"}}, {"data": {"MODE": "broken"}}):
            raise ValueError("Only config-demo data.MODE=healthy|broken is editable")
    elif kind == "services" and name == "demo-gateway":
        if patch not in ({"spec": {"selector": {"app": "net-demo"}}}, {"spec": {"selector": {"app": "no-such-demo-app"}}}):
            raise ValueError("Only the named lab Service selector is editable")
    elif kind == "deployments" and name in DEPLOYMENTS:
        if set(patch) != {"spec"}:
            raise ValueError("Deployment metadata/security edits are denied")
        spec = patch["spec"]
        if isinstance(spec, dict) and set(spec) == {"replicas"} and type(spec["replicas"]) is int and 0 <= spec["replicas"] <= 2:
            return
        try:
            template = spec["template"]
            containers = template["spec"]["containers"]
            if set(spec) != {"template"} or set(template) != {"spec"} or set(template["spec"]) != {"containers"} or len(containers) != 1:
                raise ValueError()
            container = containers[0]
            if container.get("name") != name or not set(container) <= {"name", "image", "imagePullPolicy", "command", "args", "resources"}:
                raise ValueError()
            if "image" in container and (name != "image-demo" or container["image"] not in {"busybox:1.36", "busybox:kravel-demo-image-does-not-exist"}):
                raise ValueError()
            if "imagePullPolicy" in container and container["imagePullPolicy"] not in {"Always", "IfNotPresent"}:
                raise ValueError()
            if "command" in container or "args" in container:
                pair = (container.get("command"), container.get("args"))
                allowed = [(["sh", "-c"], HEALTHY_ARGS)] if name in {"oom-demo", "crash-demo"} else []
                if name == "oom-demo":
                    allowed.append((["awk"], OOM_ARGS))
                if name == "crash-demo":
                    allowed.append((["sh", "-c"], CRASH_ARGS))
                if pair not in allowed:
                    raise ValueError()
            if "resources" in container and (name != "oom-demo" or container["resources"] not in [{"limits": {"memory": f"{size}Mi"}} for size in (32, 64, 128, 256)]):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise ValueError("Patch exceeds the reviewed lab fields/values; Pod security, volumes, commands, and credentials cannot be freely edited") from None
    else:
        raise ValueError("Manual writes are restricted to named demo labs")


def parse_command(command):
    if not isinstance(command, str) or len(command) > 5000 or "\n" in command or "\r" in command:
        raise ValueError("Submit one bounded kubectl command")
    tokens = shlex.split(command)
    if not tokens or tokens.pop(0) != "kubectl":
        raise ValueError("This is a kubectl-compatible console, not Bash. Begin with kubectl.")
    # Pin namespace; reject every credential/context override and unknown option later.
    scoped = []
    while tokens:
        item = tokens.pop(0)
        if item in {"-n", "--namespace"}:
            if not tokens or tokens.pop(0) != NAMESPACE:
                raise ValueError("Only kravel-demo is allowed")
        elif item.startswith("--namespace="):
            if item.split("=", 1)[1] != NAMESPACE:
                raise ValueError("Only kravel-demo is allowed")
        else:
            scoped.append(item)
    tokens = scoped
    if not tokens:
        raise ValueError("Command required")
    verb = tokens.pop(0)
    if verb == "logs":
        if not tokens:
            raise ValueError("Pod name required")
        pod = tokens.pop(0).removeprefix("pod/")
        _name(pod)
        flags = _flags(tokens, {"--previous": "bool", "--tail": "value", "-c": "value", "--container": "value"})
        tail = int(flags.get("--tail", 60))
        if not 1 <= tail <= 120:
            raise ValueError("Log tail must be 1..120")
        return {"verb": "logs", "kind": "pods", "name": pod, "flags": flags, "tail": tail}
    if verb == "events":
        if tokens:
            raise ValueError("Use kubectl get events for output flags")
        return {"verb": "get", "kind": "events", "name": "", "flags": {}}
    if verb in {"get", "describe"}:
        kind, name = _target(tokens)
        flags = _flags(tokens, {"-o": "value", "--output": "value", "-l": "value", "--selector": "value"})
        if any(flags.get(k, "json") not in {"json", "wide"} for k in ("-o", "--output")):
            raise ValueError("Output supports json or wide; templates/files are disabled")
        selector = flags.get("-l", flags.get("--selector", ""))
        if selector and not re.fullmatch(r"[a-zA-Z0-9_.-/]+=[a-zA-Z0-9_.-]+", selector):
            raise ValueError("Use a single key=value selector")
        if verb == "describe" and not name:
            raise ValueError("Describe requires one named resource")
        return {"verb": verb, "kind": kind, "name": name, "flags": flags}
    if verb == "rollout":
        action = tokens.pop(0) if tokens else ""
        kind, name = _target(tokens)
        if kind != "deployments" or name not in DEPLOYMENTS or tokens:
            raise ValueError("Rollout supports status/restart on named lab Deployments only")
        if action == "status":
            return {"verb": "get", "kind": kind, "name": name, "flags": {}, "rolloutStatus": True}
        if action == "restart":
            return {"verb": "patch", "kind": kind, "name": name, "patch": {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": to_iso()}}}}}, "contentType": "application/strategic-merge-patch+json"}
        raise ValueError("Unsupported rollout action")
    if verb in {"patch", "scale"}:
        kind, name = _target(tokens)
        if verb == "scale":
            flags = _flags(tokens, {"--replicas": "value"})
            patch = {"spec": {"replicas": int(flags.get("--replicas", -1))}}
        else:
            flags = _flags(tokens, {"--type": "value", "-p": "value", "--patch": "value"})
            if flags.get("--type", "strategic") not in {"strategic", "merge"}:
                raise ValueError("JSON patch and file input are disabled")
            patch = json.loads(flags.get("-p", flags.get("--patch", "{}")))
        validate_patch(kind, name, patch)
        return {"verb": "patch", "kind": kind, "name": name, "patch": patch, "contentType": "application/strategic-merge-patch+json" if kind == "deployments" else "application/merge-patch+json"}
    if verb == "set":
        action = tokens.pop(0) if tokens else ""
        kind, name = _target(tokens)
        if kind != "deployments":
            raise ValueError("Set supports lab Deployments only")
        if action == "image" and len(tokens) == 1:
            container, _, image = tokens[0].partition("=")
            patch = {"spec": {"template": {"spec": {"containers": [{"name": container, "image": image, "imagePullPolicy": "IfNotPresent"}]}}}}
        elif action == "resources":
            flags = _flags(tokens, {"--containers": "value", "--limits": "value"})
            if not flags.get("--limits", "").startswith("memory="):
                raise ValueError("Only a memory limit is editable")
            patch = {"spec": {"template": {"spec": {"containers": [{"name": flags.get("--containers", name), "resources": {"limits": {"memory": flags["--limits"].split("=", 1)[1]}}}]}}}}
        else:
            raise ValueError("Unsupported set action")
        validate_patch(kind, name, patch)
        return {"verb": "patch", "kind": kind, "name": name, "patch": patch, "contentType": "application/strategic-merge-patch+json"}
    raise ValueError("Denied: only get, describe, logs, events, and reviewed lab edits are supported")


class OperatorConsole:
    def __init__(self, kube, store):
        self.kube, self.store = kube, store
        self.lock = threading.Lock()
        self.previews = {}
        self.execution_lock = threading.Lock()

    @staticmethod
    def fingerprint(obj):
        return hashlib.sha256(stable_json({k: obj.get(k) for k in ("spec", "data", "binaryData")}).encode()).hexdigest()

    def command(self, text, session):
        if not self.execution_lock.acquire(blocking=False):
            raise ValueError("A console operation is already running; try again after it completes")
        try:
            return self._command(text, session)
        finally:
            self.execution_lock.release()

    @staticmethod
    def bounded_output(value):
        safe = public_evidence(value)
        encoded = stable_json(safe)
        return safe if len(encoded) <= 60000 else {"truncated": True, "excerpt": encoded[:60000], "note": "Output bounded to 60,000 characters. Use a named/selected resource read."}

    def _command(self, text, session):
        command = parse_command(text)
        kind, name, verb = command["kind"], command["name"], command["verb"]
        started = time.perf_counter()
        if verb == "patch":
            obj = self.kube.get_resource(kind, name, NAMESPACE)["object"]
            patch = copy.deepcopy(command["patch"])
            metadata = obj.get("metadata", {})
            if not metadata.get("uid") or not metadata.get("resourceVersion"):
                raise ValueError("Missing identity/version; refusing preview")
            patch["metadata"] = {"uid": metadata["uid"], "resourceVersion": metadata["resourceVersion"]}
            result = self.kube.patch(kind, name, NAMESPACE, patch, content_type=command["contentType"], dry_run=True)
            nonce = secrets.token_urlsafe(32)
            with self.lock:
                self.previews = {k: p for k, p in self.previews.items() if p["expires"] > time.monotonic()}
                if len(self.previews) >= 30:
                    raise ValueError("Too many pending previews")
                self.previews[nonce] = {"expires": time.monotonic()+60, "session": session, "command": command, "uid": metadata["uid"], "fingerprint": self.fingerprint(obj)}
            response = {"mode": "dry-run", "previewId": nonce, "expiresInSeconds": 60, "output": self.bounded_output(result), "note": "Manual operator action. Inspect the dry-run, then explicitly confirm within 60 seconds. Karl cannot submit or confirm this."}
        else:
            flags = command["flags"]
            if verb == "logs":
                result = self.kube.pod_logs(name, NAMESPACE, flags.get("-c", flags.get("--container", "")), flags.get("--previous", False), command["tail"])
            elif verb == "describe":
                result = self.kube.describe(kind, name, NAMESPACE)
            elif name:
                result = self.kube.get_resource(kind, name, NAMESPACE)
            else:
                result = self.kube.list_resources(kind, NAMESPACE, selector=flags.get("-l", flags.get("--selector", "")), limit=100)
            response = {"mode": "read", "output": self.bounded_output(result), "note": "One bounded API read. Rollout status is a snapshot, not a blocking watch."}
        self.store.record("operator", "console.preview" if verb == "patch" else "console.read", actor="manual-human", resource=f"{kind}/{name}", duration_ms=(time.perf_counter()-started)*1000, details={"command": public_evidence(text)})
        return response

    def confirm(self, nonce, session):
        if not self.execution_lock.acquire(blocking=False):
            raise ValueError("A console operation is already running")
        try:
            return self._confirm(nonce, session)
        finally:
            self.execution_lock.release()

    def _confirm(self, nonce, session):
        with self.lock:
            preview = self.previews.get(nonce)
            if not preview or preview["session"] != session:
                raise ValueError("Preview unavailable for this session")
            self.previews.pop(nonce)  # one-use even on a failed/stale apply
        if preview["expires"] <= time.monotonic():
            raise ValueError("Preview expired; run a new dry-run")
        command = preview["command"]
        kind, name = command["kind"], command["name"]
        obj = self.kube.get_resource(kind, name, NAMESPACE)["object"]
        if obj.get("metadata", {}).get("uid") != preview["uid"] or self.fingerprint(obj) != preview["fingerprint"]:
            raise ValueError("Resource changed after preview; run a new dry-run")
        patch = copy.deepcopy(command["patch"])
        patch["metadata"] = {"uid": preview["uid"], "resourceVersion": obj["metadata"]["resourceVersion"]}
        result = self.kube.patch(kind, name, NAMESPACE, patch, content_type=command["contentType"], dry_run=False)
        self.store.record("operator", "console.applied", actor="manual-human", resource=f"{kind}/{name}", duration_ms=result.get("durationMs", 0), details={"patch": public_evidence(command["patch"]), "approval": "manual-console-confirmation"})
        return {"mode": "applied", "output": self.bounded_output(result), "note": "API accepted this manual change. Watch the live view for rollout/readiness."}


def create_operator_server(console, config):
    sessions, failures, lock = {}, {}, threading.Lock()
    assets = {"/": ("operator.html", "text/html"), "/ui/operator.js": ("operator.js", "text/javascript"), "/ui/operator.css": ("operator.css", "text/css")}
    root = Path(__file__).resolve().parent / "web"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def session(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                key = cookie["kravel_operator"].value
            except (KeyError, ValueError):
                return ""
            with lock:
                return key if sessions.get(key, 0) > time.monotonic() else ""

        def send(self, status, payload, cookie=""):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in {"/healthz", "/readyz"}:
                return self.send(200, {"status": "ready"})
            if path == "/metrics":
                body = prometheus_metrics(console.store, "operator").encode()
                self.send_response(200); self.send_header("Content-Type", "text/plain"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            if path == "/v1/audit":
                # Sanitized action metadata only, also consumed by the read-only audit panel.
                return self.send(200, {"entries": console.store.audit_entries(100)})
            if path in assets:
                filename, mime = assets[path]
                body = (root/filename).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mime+"; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors http://127.0.0.1:8080; base-uri 'none'; form-action 'none'")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers(); self.wfile.write(body); return
            if path == "/v1/console/session":
                return self.send(200, {"unlocked": bool(self.session()), "namespace": NAMESPACE})
            return self.send(404, {"error": "not_found"})

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                if self.headers.get("Origin") != ORIGIN or self.headers.get("Host") != "127.0.0.1:8082":
                    console.store.record("operator", "console.auth_denied", actor="unauthenticated", outcome="denied", details={"reason": "origin_or_host"})
                    return self.send(403, {"error": "Exact console origin required; no cross-origin command access"})
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 12000 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("Bounded JSON body required")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
                if path == "/v1/console/unlock":
                    with lock:
                        now = time.monotonic()
                        attempts = [at for at in failures.get(self.client_address[0], []) if now-at < 60]
                        failures[self.client_address[0]] = attempts
                        if len(attempts) >= 10:
                            return self.send(429, {"error": "Too many unlock attempts; wait one minute"})
                        if not config.operator_token or not hmac.compare_digest(str(body.get("token", "")), config.operator_token):
                            attempts.append(now)
                            console.store.record("operator", "console.auth_denied", actor="unauthenticated", outcome="denied", details={"reason": "unlock_key"})
                            return self.send(401, {"error": "Console unlock key required"})
                        sessions_to_remove = [key for key, expiry in sessions.items() if expiry <= now]
                        for key in sessions_to_remove:
                            sessions.pop(key)
                        if len(sessions) >= 20:
                            return self.send(429, {"error": "Too many sessions; wait for expiry"})
                        session = secrets.token_urlsafe(32)
                        sessions[session] = now+900
                    console.store.record("operator", "console.unlocked", actor="manual-human", details={"sessionSeconds": 900})
                    return self.send(200, {"unlocked": True}, f"kravel_operator={session}; HttpOnly; SameSite=Strict; Path=/v1/console/; Max-Age=900")
                session = self.session()
                if not session:
                    console.store.record("operator", "console.auth_denied", actor="unauthenticated", outcome="denied", details={"reason": "missing_or_expired_session"})
                    return self.send(401, {"error": "Unlock the human console first (15-minute session)"})
                if path == "/v1/console/lock":
                    with lock:
                        sessions.pop(session, None)
                    console.store.record("operator", "console.locked", actor="manual-human")
                    return self.send(200, {"unlocked": False}, "kravel_operator=; HttpOnly; SameSite=Strict; Path=/v1/console/; Max-Age=0")
                if path == "/v1/console/command":
                    return self.send(200, console.command(body.get("command", ""), session))
                if path == "/v1/console/confirm":
                    return self.send(200, console.confirm(str(body.get("previewId", "")), session))
                return self.send(404, {"error": "not_found"})
            except Exception as exc:
                console.store.record("operator", "console.denied", actor="manual-human", outcome="denied", details={"errorType": type(exc).__name__})
                return self.send(400, {"error": public_evidence(str(exc))})

    return ThreadingHTTPServer((config.host, config.port), Handler)
