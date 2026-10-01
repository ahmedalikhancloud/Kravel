from contextlib import contextmanager
import json
import threading
import urllib.error
import urllib.request

from kravel.api import create_server
from kravel.broker import ApprovalBroker, create_broker_server
from kravel.config import load_config
from kravel.store import AuditStore


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def post(base, path, body, headers=None):
    request = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def test_read_api_audits_failed_and_successful_tools_and_has_no_approval_route():
    config = load_config()
    config.host, config.port = "127.0.0.1", 0
    store = AuditStore()

    class Kube:
        def get_resource(self, *_):
            return {"object": {"kind": "ConfigMap"}}

    with serving(create_server(store, config, Kube())) as base:
        with urllib.request.urlopen(base + "/v1/resource?kind=configmaps&name=config-demo") as response:
            assert json.load(response)["object"]["kind"] == "ConfigMap"
        status, _ = post(base, "/v1/tools/run", {"tool": "get_resource", "arguments": {"kind": "secrets", "name": "forbidden"}})
        assert status == 400
        assert post(base, "/v1/proposals/id/approve", {})[0] == 404
    entries = store.audit_entries()
    assert {item["outcome"] for item in entries if item["action"] == "tool.get_resource"} == {"success", "error"}


def test_broker_rejects_missing_approval_credential(monkeypatch):
    monkeypatch.setattr(ApprovalBroker, "_start_waiter", lambda *_: None)
    config = load_config()
    config.host, config.port = "127.0.0.1", 0
    config.approval_token = "local-test-credential"
    config.slack_bot_token = config.slack_channel_id = ""
    store = AuditStore()
    with serving(create_broker_server(ApprovalBroker(object(), store, config), config)) as base:
        assert post(base, "/v1/proposals/id/approve", {})[0] == 401
        assert post(base, "/v1/proposals/id/approve", {}, {"X-Kravel-Approval-Token": "incorrect"})[0] == 401
    assert sum(item["action"] == "approval.denied" for item in store.audit_entries()) == 2


def test_local_3d_assets_are_served_with_strict_csp_without_remote_scripts():
    config = load_config()
    config.host, config.port = "127.0.0.1", 0
    with serving(create_server(AuditStore(), config, object())) as base:
        for path in ("/ui/scene.js", "/ui/topology.mjs", "/ui/demo.mjs", "/ui/vendor/three.module.min.js", "/ui/vendor/three.core.min.js", "/ui/vendor/OrbitControls.js"):
            with urllib.request.urlopen(base + path) as response:
                assert response.status == 200
                assert "text/javascript" in response.headers["Content-Type"]
                assert "script-src 'self'" in response.headers["Content-Security-Policy"]
                assert "unsafe-inline" not in response.headers["Content-Security-Policy"]
                assert len(response.read()) > 100


def test_fresh_view_refuses_to_hide_active_approvals_and_preserves_history(monkeypatch):
    import kravel.api as api
    config = load_config()
    config.host, config.port = '127.0.0.1', 0
    store = AuditStore()
    store.start_workflow('old-run', 'investigation', 'kravel-demo')
    monkeypatch.setattr(api, '_broker_request', lambda *_: {'proposals': [{'status': 'pending'}]})
    with serving(create_server(store, config, object())) as base:
        assert post(base, '/v1/demo-session', {})[0] == 409
        monkeypatch.setattr(api, '_broker_request', lambda *_: {'proposals': []})
        status, body = post(base, '/v1/demo-session', {})
        assert status == 200 and body['historyPreserved'] is True and body['clusterReset'] is False
        assert store.workflow('old-run')


def test_broker_fresh_view_requires_human_key_and_preserves_records(monkeypatch):
    monkeypatch.setattr(ApprovalBroker, '_start_waiter', lambda *_: None)
    config = load_config()
    config.host, config.port = '127.0.0.1', 0
    config.approval_token = 'local-test-credential'
    config.slack_bot_token = config.slack_channel_id = ''
    store = AuditStore()
    broker = ApprovalBroker(object(), store, config)
    with serving(create_broker_server(broker, config)) as base:
        original = store.demo_session()
        assert post(base, '/v1/demo-session', {})[0] == 401
        store.create_proposal(id='active', fix_id='fix_image_pull', namespace='kravel-demo', resource='Deployment/image-demo', command='reviewed', dry_run=[], expires_at='2099-01-01T00:00:00Z')
        headers = {'X-Kravel-Approval-Token': config.approval_token}
        assert post(base, '/v1/demo-session', {}, headers)[0] == 409
        store.update_proposal('active', status='rejected')
        status, body = post(base, '/v1/demo-session', {}, headers)
        assert status == 200 and body['historyPreserved'] is True
        assert body['session']['id'] != original['id'] and store.proposal('active')
