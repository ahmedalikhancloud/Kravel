import json
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace

from kravel.api import create_server
from kravel.laya import build_classifier_evidence
from kravel.store import TemporalStore


def _seed(store):
    baseline = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "api", "namespace": "demo", "resourceVersion": "1"}, "spec": {"template": {"spec": {"containers": [{"name": "api", "image": "example/api:1"}]}}}}
    incident = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "api", "namespace": "demo", "resourceVersion": "2"}, "spec": {"template": {"spec": {"containers": [{"name": "api", "image": "example/missing:2"}]}}}}
    store.record_resource_change(cluster_id="test", action="ADDED", object=baseline, event_at="2026-01-01T00:00:01Z")
    store.record_resource_change(cluster_id="test", action="MODIFIED", object=incident, event_at="2026-01-01T00:00:03Z")
    store.record_kubernetes_event("test", {"metadata": {"uid": "event-1", "namespace": "demo", "creationTimestamp": "2026-01-01T00:00:04Z"}, "regarding": {"kind": "Pod", "name": "api-bad", "namespace": "demo"}, "type": "Warning", "reason": "ImagePullBackOff", "note": "manifest unknown"})


def test_incident_shards_use_deterministic_kubernetes_signals():
    store = TemporalStore()
    try:
        _seed(store)
        evidence = build_classifier_evidence(store, "test", "2026-01-01T00:00:02Z", "2026-01-01T00:00:05Z", "demo")
        image = next(shard for shard in evidence["shards"] if shard["diagnosis"] == "bad_image_rollout")
        assert image["source"] == "kubernetes_signal"
        assert image["score"] == 0.99
        assert "bad_image_rollout" not in evidence["layaQuestions"]
    finally:
        store.close()


def test_local_ui_and_grounded_karl_endpoints():
    store = TemporalStore()
    _seed(store)
    config = SimpleNamespace(api_token="", max_body_bytes=1_000_000, cluster_id="test", host="127.0.0.1", port=0)
    server = create_server(store, config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        html = urllib.request.urlopen(base + "/", timeout=3).read().decode()
        timeline = json.load(urllib.request.urlopen(base + "/v1/timeline?namespace=demo", timeout=3))
        request = urllib.request.Request(base + "/v1/karl/chat", data=json.dumps({"action": "greet", "at": "2026-01-01T00:00:05Z", "namespace": "demo"}).encode(), headers={"content-type": "application/json"})
        greeting = json.load(urllib.request.urlopen(request, timeout=3))
        assert "Temporal Cluster Cockpit" in html
        assert timeline["entryCount"] >= 3
        assert greeting["kind"] == "greeting"
        denied = urllib.request.Request(base + "/v1/karl/analyze", data=json.dumps({"approved": False}).encode(), headers={"content-type": "application/json"})
        try:
            urllib.request.urlopen(denied, timeout=3)
            assert False, "analysis should require explicit approval"
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
    finally:
        server.shutdown()
        server.server_close()
        store.close()
