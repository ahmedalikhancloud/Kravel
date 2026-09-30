from contextlib import contextmanager
from copy import deepcopy
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from kravel.config import load_config
from kravel.operator import OperatorConsole, create_operator_server, parse_command, validate_patch
from kravel.store import AuditStore


class Kube:
    def __init__(self):
        self.obj = {"kind": "Deployment", "metadata": {"name": "image-demo", "uid": "demo-uid", "resourceVersion": "1"}, "spec": {"replicas": 1}}
        self.calls = []

    def get_resource(self, *_):
        return {"object": deepcopy(self.obj)}

    def patch(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"object": deepcopy(self.obj), "dryRun": kwargs["dry_run"]}

    def list_resources(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"items": [{"data": {"password": "do-not-show", "MODE": "healthy"}}]}


@pytest.mark.parametrize("command", [
    "kubectl get secrets", "kubectl get nodes", "kubectl get pods -A",
    "kubectl --context=production get pods", "kubectl get pods --namespace=default",
    "kubectl get pods --as=root", "kubectl get pods --token=anything",
    "kubectl get pods -o jsonpath={.items}", "kubectl logs pod-a --follow",
    "kubectl exec pod-a -- sh", "kubectl delete pod pod-a", "kubectl apply -f https://example.invalid",
    "kubectl get pods | sh", "kubectl get pods && kubectl delete ns default",
    "bash demo/local/reset.sh", "kubectl scale deployment/kravel --replicas=1",
    "kubectl set image deployment/image-demo image-demo=unreviewed:image",
    "kubectl patch deployment image-demo --type=json -p '[]'",
])
def test_unsafe_commands_denied(command):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        parse_command(command)


@pytest.mark.parametrize("patch", [
    {"spec": {"template": {"spec": {"hostNetwork": True}}}},
    {"spec": {"template": {"spec": {"serviceAccountName": "admin"}}}},
    {"spec": {"template": {"spec": {"containers": [{"name": "image-demo", "securityContext": {"privileged": True}}]}}}},
    {"spec": {"template": {"spec": {"containers": [{"name": "image-demo", "command": ["sh", "-c"], "args": ["arbitrary shell"]}]}}}},
    {"metadata": {"annotations": {"anything": "value"}}},
    {"spec": {"replicas": 100}},
])
def test_unreviewed_patch_fields_cannot_escape(patch):
    with pytest.raises(ValueError):
        validate_patch("deployments", "image-demo", patch)


def test_manual_write_dry_run_confirm_once_and_stale_detection():
    kube, store = Kube(), AuditStore()
    console = OperatorConsole(kube, store)
    command = "kubectl set image deployment/image-demo image-demo=busybox:1.36"
    preview = console.command(command, "session-one")
    assert all(call[1]["dry_run"] for call in kube.calls)
    with pytest.raises(ValueError):
        console.confirm(preview["previewId"], "different-session")
    console.confirm(preview["previewId"], "session-one")
    assert sum(not call[1]["dry_run"] for call in kube.calls) == 1
    with pytest.raises(ValueError):
        console.confirm(preview["previewId"], "session-one")
    preview = console.command(command, "session-one")
    kube.obj["spec"]["replicas"] = 2
    with pytest.raises(ValueError, match="changed"):
        console.confirm(preview["previewId"], "session-one")
    assert sum(not call[1]["dry_run"] for call in kube.calls) == 1
    assert store.audit_entries()[1]["actor"] == "manual-human"


def test_expired_preview_and_redacted_reads():
    kube, store = Kube(), AuditStore()
    console = OperatorConsole(kube, store)
    preview = console.command("kubectl set image deployment/image-demo image-demo=busybox:1.36", "session")
    console.previews[preview["previewId"]]["expires"] = time.monotonic()-1
    with pytest.raises(ValueError, match="expired"):
        console.confirm(preview["previewId"], "session")
    assert "do-not-show" not in json.dumps(console.command("kubectl get configmaps", "session"))
    assert all(call[1].get("dry_run", True) for call in kube.calls)


def test_console_http_separate_auth_origin_cookie_and_no_parent_access():
    config = load_config()
    config.host, config.port, config.operator_token = "127.0.0.1", 0, "unit-test-console-key"
    server = create_operator_server(OperatorConsole(Kube(), AuditStore()), config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path, body, origin="http://127.0.0.1:8082", cookie=""):
        request = urllib.request.Request(base+path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Host": "127.0.0.1:8082", "Origin": origin, "Cookie": cookie})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, dict(response.headers), json.load(response)
        except urllib.error.HTTPError as response:
            return response.code, dict(response.headers), json.load(response)
    try:
        assert post("/v1/console/command", {"command": "kubectl get pods"})[0] == 401
        assert post("/v1/console/unlock", {"token": config.operator_token}, origin="http://127.0.0.1:8080")[0] == 403
        assert post("/v1/console/unlock", {"token": "wrong"})[0] == 401
        status, headers, _ = post("/v1/console/unlock", {"token": config.operator_token})
        assert status == 200
        cookie = headers["Set-Cookie"]
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/v1/console/" in cookie
        assert "Access-Control-Allow-Origin" not in headers
        cookie = cookie.split(";", 1)[0]
        assert post("/v1/console/command", {"command": "kubectl get pods"}, cookie=cookie)[0] == 200
        assert post("/v1/console/command", {"command": "kubectl get pods"}, origin="http://127.0.0.1:8080", cookie=cookie)[0] == 403
        assert post("/v1/console/lock", {}, cookie=cookie)[0] == 200
        assert post("/v1/console/command", {"command": "kubectl get pods"}, cookie=cookie)[0] == 401
    finally:
        server.shutdown(); server.server_close(); thread.join()
