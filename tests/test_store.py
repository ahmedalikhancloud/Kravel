from kravel.store import TemporalStore


def obj(version, mode="healthy"):
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "api-config", "namespace": "demo", "resourceVersion": str(version)}, "data": {"STARTUP_MODE": mode}}


def test_time_travel_and_diff_are_exact():
    store = TemporalStore()
    try:
        store.record_resource_change(cluster_id="c", action="ADDED", object=obj(1), event_at="2026-01-01T00:00:01Z")
        store.record_resource_change(cluster_id="c", action="MODIFIED", object=obj(2, "broken"), event_at="2026-01-01T00:00:02Z")
        before = store.state_at(cluster_id="c", timestamp="2026-01-01T00:00:01.500Z", namespace="demo")
        after = store.state_at(cluster_id="c", timestamp="2026-01-01T00:00:03Z", namespace="demo")
        assert before["objects"][0]["data"]["STARTUP_MODE"] == "healthy"
        assert after["objects"][0]["data"]["STARTUP_MODE"] == "broken"
        diff = store.diff_states(cluster_id="c", from_at="2026-01-01T00:00:01.500Z", to_at="2026-01-01T00:00:03Z", namespace="demo")
        assert diff["changeCount"] == 1
        assert "/data/STARTUP_MODE" in diff["changes"][0]["changedPaths"]
    finally:
        store.close()


def test_out_of_order_insert_repairs_the_following_patch():
    store = TemporalStore()
    try:
        store.record_resource_change(cluster_id="c", action="ADDED", object=obj(1), event_at="2026-01-01T00:00:01Z")
        store.record_resource_change(cluster_id="c", action="MODIFIED", object=obj(3, "broken"), event_at="2026-01-01T00:00:03Z")
        store.record_resource_change(cluster_id="c", action="MODIFIED", object=obj(2, "warmup"), event_at="2026-01-01T00:00:02Z")
        context = store.context_shard(cluster_id="c", incident_at="2026-01-01T00:00:04Z", lookback=10, namespace="demo")
        final = next(change for change in context["changes"] if change["eventAt"] == "2026-01-01T00:00:03.000Z")
        assert final["patch"] == [{"op": "replace", "path": "/data/STARTUP_MODE", "value": "broken"}, {"op": "replace", "path": "/metadata/resourceVersion", "value": "3"}]
    finally:
        store.close()


def test_secret_payload_is_redacted():
    store = TemporalStore()
    try:
        secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "db", "namespace": "demo", "resourceVersion": "1"}, "data": {"password": "base64-secret"}}
        store.record_resource_change(cluster_id="c", action="ADDED", object=secret, event_at="2026-01-01T00:00:01Z")
        state = store.state_at(cluster_id="c", timestamp="2026-01-01T00:00:02Z")
        assert state["objects"][0]["data"]["password"] == "<redacted>"
    finally:
        store.close()
