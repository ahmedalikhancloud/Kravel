from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from kravel.config import load_config
from kravel.tracing import MlflowTracer, trace_content, select_experiment


def test_http_artifact_migration_keeps_legacy_experiment_and_uses_new_identity():
    old = SimpleNamespace(name="Kravel", experiment_id="1", artifact_location="/mlflow/artifacts/1")
    new = SimpleNamespace(name="Kravel (HTTP artifacts)", experiment_id="3", artifact_location="mlflow-artifacts:/3")
    class Experiments:
        def get_experiment_by_name(self, name):
            return old if name == old.name else new
        def set_experiment(self, name):
            self.selected = name
            return self.get_experiment_by_name(name)
    sdk = Experiments()
    assert select_experiment(sdk, "Kravel", "http://localhost:5000") is new
    assert old.name == "Kravel" and old.artifact_location == "/mlflow/artifacts/1"
    assert select_experiment(sdk, "Kravel", "sqlite:///fixture.db") is old
    new.artifact_location = "/still-not-shared"
    with pytest.raises(RuntimeError, match="artifact serving"):
        select_experiment(sdk, "Kravel", "http://localhost:5000")


class LiveSpan:
    trace_id = "test-trace"

    def __init__(self):
        self.inputs, self.outputs, self.attributes = {}, {}, {}

    def set_inputs(self, value):
        self.inputs = deepcopy(value)

    def set_outputs(self, value):
        self.outputs = deepcopy(value)

    def set_attribute(self, key, value):
        self.attributes[key] = value


class SDK:
    def __init__(self):
        self.live = LiveSpan()
        self.error = None
        self.previews = {}

    def update_current_trace(self, **value):
        self.previews.update(value)

    @contextmanager
    def start_span(self, **_):
        try:
            yield self.live
        except Exception as exc:
            self.error = str(exc)
            raise


def tracer(mode):
    exporter = MlflowTracer("", "test", mode)
    exporter.enabled, exporter._mlflow = True, SDK()
    return exporter


def test_redacted_content_merges_question_answer_and_metadata():
    exporter = tracer("redacted")
    with exporter.span("agent", inputs={"namespace": "kravel-demo"}) as span:
        span.set_content_inputs({"question": "Why is image-demo failing? password=example-sensitive-value"})
        span.set_outputs({"status": "success"})
        span.set_content_outputs({"diagnosis": "Image tag does not exist. Uncertainty: registry untested."})
    live = exporter._mlflow.live
    assert live.inputs["namespace"] == "kravel-demo"
    assert "Why is image-demo failing?" in live.inputs["question"]
    assert "example-sensitive-value" not in live.inputs["question"]
    assert live.outputs["status"] == "success" and "Image tag" in live.outputs["diagnosis"]
    assert live.attributes["kravel.content_mode"] == "redacted"
    assert exporter.overhead_ms > 0


def test_metadata_mode_never_exports_question_or_results():
    exporter = tracer("metadata")
    with exporter.span("agent", inputs={"namespace": "kravel-demo"}) as span:
        span.set_content_inputs({"question": "Do not export me"})
        span.set_outputs({"status": "success"})
        span.set_content_outputs({"logs": "Do not export these logs"})
    assert exporter._mlflow.live.inputs == {"namespace": "kravel-demo"}
    assert exporter._mlflow.live.outputs == {"status": "success"}


def test_serialized_arguments_env_fields_urls_and_provider_keys_are_redacted():
    fake_key = "gsk_" + "a" * 35  # synthetic test value, never a usable key
    payload = {
        "messages": [{"tool_calls": [{"function": {"arguments": json.dumps({"password": "hidden-json-value", "name": "image-demo"})}}]}],
        "env": [{"name": "API_KEY", "value": "hidden-env-value"}],
        "authorization": "hidden-auth-value",
        "question": f"Explain the failure; {fake_key}",
        "url": "https://example.invalid/path?token=hidden-query-value",
        "json_text": 'The request included "password": "hidden-inline-value"',
    }
    safe = trace_content(payload)
    encoded = json.dumps(safe)
    assert "image-demo" in encoded
    assert all(value not in encoded for value in (fake_key, "hidden-json-value", "hidden-env-value", "hidden-auth-value", "hidden-query-value", "hidden-inline-value"))


def test_payload_size_and_depth_are_bounded_and_marked():
    safe = trace_content({f"field{i}": "x" * 5000 for i in range(10)})
    assert safe["content_truncated"] is True
    assert len(json.dumps(safe)) < 25_000
    deeply_nested = {}
    for _ in range(30):
        deeply_nested = {"nested": deeply_nested}
    assert "trace nesting limit" in json.dumps(trace_content(deeply_nested))


def test_exporter_errors_do_not_break_investigations_and_exceptions_are_sanitized():
    exporter = tracer("redacted")
    def fail(_):
        raise RuntimeError("Exporter unavailable")
    exporter._mlflow.live.set_outputs = fail
    with exporter.span("agent") as span:
        span.set_content_outputs({"diagnosis": "Still usable"})
    assert exporter.error == "RuntimeError"

    exporter = tracer("redacted")
    with pytest.raises(RuntimeError, match="sensitive-value"):
        with exporter.span("agent"):
            raise RuntimeError("password=sensitive-value")
    assert "sensitive-value" not in exporter._mlflow.error


def test_content_mode_defaults_to_metadata_and_rejects_raw(monkeypatch):
    monkeypatch.delenv("KRAVEL_MLFLOW_CONTENT_MODE", raising=False)
    assert load_config().mlflow_content_mode == "metadata"
    monkeypatch.setenv("KRAVEL_MLFLOW_CONTENT_MODE", "redacted")
    assert load_config().mlflow_content_mode == "redacted"
    monkeypatch.setenv("KRAVEL_MLFLOW_CONTENT_MODE", "raw")
    with pytest.raises(ValueError, match="metadata or redacted"):
        load_config()
    with pytest.raises(ValueError):
        MlflowTracer("", "test", "raw")


def test_preview_shows_useful_text_without_credentials_and_metadata_mode_omits_it():
    exporter = tracer("redacted")
    exporter.set_previews(question="Why did it crash? password=preview-private-value", diagnosis="Finding: startup failure. " + "x" * 1200)
    assert exporter._mlflow.previews["request_preview"].startswith("Why did it crash?")
    assert "preview-private-value" not in json.dumps(exporter._mlflow.previews)
    assert len(exporter._mlflow.previews["response_preview"]) < 1000
    assert "preview truncated" in exporter._mlflow.previews["response_preview"]
    exporter = tracer("metadata")
    exporter.set_previews(question="Do not export")
    assert exporter._mlflow.previews == {}
