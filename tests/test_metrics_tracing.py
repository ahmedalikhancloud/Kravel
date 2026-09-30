from contextlib import contextmanager

import mlflow

from kravel.metrics import prometheus_metrics
from kravel.store import TemporalStore
from kravel.tracing import MlflowTracer


def test_tracer_creates_real_nested_spans_without_raw_payloads(monkeypatch):
    seen = []
    class FakeSpan:
        trace_id = "tr-test"
        def set_inputs(self, value): seen.append(("inputs", value))
        def set_outputs(self, value): seen.append(("outputs", value))
        def set_attribute(self, key, value): seen.append((key, value))
    @contextmanager
    def start_span(**kwargs):
        seen.append(("span", kwargs))
        yield FakeSpan()
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda value: seen.append(("uri", value)))
    monkeypatch.setattr(mlflow, "set_experiment", lambda value: seen.append(("experiment", value)))
    monkeypatch.setattr(mlflow, "start_span", start_span)
    monkeypatch.setattr(mlflow, "flush_trace_async_logging", lambda: seen.append(("flush", True)))
    tracer = MlflowTracer("http://kravel-mlflow:5000", "Kravel tests")
    with tracer.span("kravel.incident_pipeline", "AGENT", {"change_count": 2}) as root:
        with tracer.span("langgraph.guardrail", "GUARDRAIL", {"input_characters": 140}) as child:
            child.set_outputs({"decision": "allow"})
        root.set_outputs({"route": "qwen_investigation"})
    assert tracer.flush() >= 0
    assert tracer.trace_id == "tr-test"
    assert [item[1]["name"] for item in seen if item[0] == "span"] == ["kravel.incident_pipeline", "langgraph.guardrail"]
    assert ("outputs", {"route": "qwen_investigation"}) in seen
    assert ("flush", True) in seen
    assert not any("secret" in str(item).lower() for item in seen)


def test_prometheus_metrics_include_trace_and_stage_latency():
    store = TemporalStore()
    try:
        store.record_benchmark_run(comparison_id="run", cluster_id="c", flow="incident_pipeline", provider="local", model="laya+qwen", status="success", started_at="2026-01-01T00:00:00Z", finished_at="2026-01-01T00:00:01Z", total_ms=1000, stage_metrics={"qwen_inference": 900}, trace_id="tr-123")
        metrics = prometheus_metrics(store, "c")
        assert 'stage="qwen_inference"' in metrics
        assert 'trace_id="tr-123"' in metrics
        assert 'phase="end_to_end"' in metrics
    finally:
        store.close()
