from contextlib import contextmanager
import json

from kravel.config import load_config
from kravel.pipeline import run_incident_pipeline
from kravel.store import TemporalStore
from kravel.agent import _merge_usage, compact_tool_result


class Span:
    def set_outputs(self, _value): pass
    def set_inputs(self, _value): pass
    def set_attribute(self, _key, _value): pass


class Tracer:
    @contextmanager
    def span(self, *_args, **_kwargs):
        yield Span()


def test_usage_merge_supports_new_openai_nested_token_details():
    merged = _merge_usage(
        {"prompt_tokens": 10, "completion_tokens_details": {"reasoning_tokens": 0}},
        {"prompt_tokens": 4, "completion_tokens_details": {"reasoning_tokens": 3, "accepted_prediction_tokens": 2}},
    )
    assert merged["prompt_tokens"] == 14
    assert merged["completion_tokens_details"]["reasoning_tokens"] == 3
    assert merged["completion_tokens_details"]["accepted_prediction_tokens"] == 2


def test_tool_compaction_prioritizes_causal_config_and_removes_status_noise():
    raw = {"from": "a", "to": "b", "changeCount": 2, "changes": [
        {"resourceKey": "apps/v1|Deployment|demo|api", "changeType": "modified", "patch": [{"op": "replace", "path": "/status/readyReplicas", "value": 0}]},
        {"resourceKey": "v1|ConfigMap|demo|api-config", "changeType": "modified", "patch": [{"op": "replace", "path": "/data/STARTUP_MODE", "value": "broken"}, {"op": "replace", "path": "/metadata/resourceVersion", "value": "2"}]},
    ]}
    compact = compact_tool_result("diff_states", raw)
    assert compact["changes"] == [{"resourceKey": "v1|ConfigMap|demo|api-config", "changeType": "modified", "operations": [{"op": "replace", "path": "/data/STARTUP_MODE", "value": "broken"}]}]


def test_context_compaction_preserves_warning_events_with_large_created_objects():
    huge_pod = {"kind": "Pod", "metadata": {"name": "pod-1", "namespace": "demo"}, "spec": {"payload": "x" * 10_000}}
    raw = {"window": {"start": "a", "incidentAt": "b"}, "changes": [
        {"eventAt": "2026-01-01T00:00:01Z", "action": "ADDED", "resourceKey": "v1|Pod|demo|pod-1", "patch": [{"op": "add", "path": "", "value": huge_pod}]},
        {"eventAt": "2026-01-01T00:00:00Z", "action": "MODIFIED", "resourceKey": "v1|ConfigMap|demo|api-config", "patch": [{"op": "replace", "path": "/data/STARTUP_MODE", "value": "broken"}]},
    ], "kubernetesEvents": [{"eventAt": "2026-01-01T00:00:02Z", "type": "Warning", "reason": "BackOff", "regardingKind": "Pod", "regardingName": "pod-1", "note": "Back-off restarting failed container"}], "caveats": []}
    compact = compact_tool_result("get_incident_context", raw)
    encoded = json.dumps(compact)
    assert len(encoded) < 3500
    assert compact["changes"][0]["operations"][0]["value"] == "broken"
    assert compact["kubernetesEvents"][0]["reason"] == "BackOff"


def seed(store):
    baseline = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "api-config", "namespace": "demo", "resourceVersion": "1"}, "data": {"STARTUP_MODE": "healthy"}}
    incident = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "api-config", "namespace": "demo", "resourceVersion": "2"}, "data": {"STARTUP_MODE": "broken"}}
    store.record_resource_change(cluster_id="test", action="ADDED", object=baseline, event_at="2026-01-01T00:00:01Z")
    store.record_resource_change(cluster_id="test", action="MODIFIED", object=incident, event_at="2026-01-01T00:00:02Z")


def test_langgraph_routine_path(monkeypatch):
    store, config = TemporalStore(), load_config()
    config.cluster_id = "test"
    monkeypatch.setattr("kravel.pipeline.run_laya_classifier", lambda *_args: {"model": "english", "modelMs": 2.0, "confidence": 0.97, "diagnosis": {"config_regression": 0.97, "service_selector_drift": 0.01, "bad_image_rollout": 0.01, "scheduling_constraint": 0.01}})
    try:
        seed(store)
        result = run_incident_pipeline(store, config, Tracer(), "2026-01-01T00:00:01.500Z", "2026-01-01T00:00:03Z", "demo", "routine")
        assert result["route"] == "predefined_runbook"
        assert result["qwen"] is None
        assert result["proposal"]["remediationExecuted"] is False
    finally:
        store.close()


def test_langgraph_escalation_path(monkeypatch):
    store, config = TemporalStore(), load_config()
    config.cluster_id = "test"
    monkeypatch.setattr("kravel.pipeline.run_laya_classifier", lambda *_args: {"model": "english", "modelMs": 2.0, "confidence": 0.95, "diagnosis": {"config_regression": 0.01, "service_selector_drift": 0.95, "bad_image_rollout": 0.01, "scheduling_constraint": 0.01}})
    monkeypatch.setattr("kravel.pipeline.run_temporal_agent", lambda *_args: {"answer": "At 2026-01-01T00:00:02Z evidence changed. Uncertainty remains.", "model": "qwen", "turns": 1, "toolCalls": 2, "toolAttempts": 2, "modelMs": 3.0, "toolMs": 1.0, "inputGuardrailMs": 0.2, "usage": {}, "inputGuardrail": {"decision": "allow", "findings": []}})
    try:
        seed(store)
        result = run_incident_pipeline(store, config, Tracer(), "2026-01-01T00:00:01.500Z", "2026-01-01T00:00:03Z", "demo", "escalation")
        assert result["route"] == "qwen_investigation"
        assert result["qwen"]["toolCalls"] == 2
        assert result["reviewStatus"] == "awaiting_human_review"
    finally:
        store.close()
