from __future__ import annotations

from contextlib import contextmanager
import json
import re
import time

from .guardrails import public_evidence, guard_model_input, _normalize, _redact
from .utils import safe_service_url, stable_json


def select_experiment(mlflow, name, tracking_uri):
    """Keep legacy experiments intact; remote workers need proxied artifacts."""
    remote = tracking_uri.startswith(("http://", "https://"))
    # set_experiment is available in both mlflow-tracing and the full SDK.
    # get_experiment_by_name is not exported by the tracing-only package.
    experiment = mlflow.set_experiment(name)
    if remote and not experiment.artifact_location.startswith("mlflow-artifacts:"):
        experiment = mlflow.set_experiment(name + " (HTTP artifacts)")
    if remote and not experiment.artifact_location.startswith("mlflow-artifacts:"):
        raise RuntimeError("Configure MLflow artifact serving before running cross-pod evaluation")
    return experiment


def trace_content(value, max_characters=24_000, text_limit=6000, quarantine_instructions=True):
    """Bounded, best-effort redaction, including serialized tool args and env values."""
    sensitive = re.compile(r"(?i)password|passwd|token|secret|api.?key|authorization|credential")
    counters = {"prompt_tokens", "completion_tokens", "total_tokens", "max_tokens", "max_completion_tokens", "input_tokens", "output_tokens", "cached_tokens",
                "mlflow.assessment.judgeInputTokens", "mlflow.assessment.judgeOutputTokens"}

    def numeric_counter(key, value):
        # Only known usage counters may bypass credential-key redaction. MLflow
        # stores assessment metadata as strings; never exempt arbitrary text.
        return key in counters and (type(value) in (int, float) or isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,6})?", value))

    def clean(item, depth=0):
        if depth > 12:
            return "[trace nesting limit]"
        if isinstance(item, dict):
            named_credential = sensitive.search(str(item.get("name", "")))
            return {str(k)[:200]: "<redacted:sensitive_field>" if (sensitive.search(str(k)) and not numeric_counter(k, v)) or named_credential and k == "value" else clean(v, depth+1) for k, v in list(item.items())[:80]}
        if isinstance(item, list):
            return [clean(v, depth+1) for v in item[:100]]
        if isinstance(item, str):
            if item.lstrip().startswith(("{", "[")):
                if len(item) > 192_000:
                    return "[oversized serialized trace content withheld]"
                try:
                    parsed = json.loads(item)
                    if isinstance(parsed, (dict, list)):
                        # Preserve wire text types (chat content / tool arguments /
                        # retrieved page_content) after recursively redacting JSON.
                        item = stable_json(clean(parsed, depth+1))
                except (ValueError, RecursionError):
                    pass
            if not item.strip():
                return item
            if not quarantine_instructions:
                redacted = _redact(_normalize(item))[0]
                return redacted if len(redacted) <= text_limit else redacted[:text_limit-40] + "\n[trace text truncated]"
            return guard_model_input(item, "trace", text_limit)["value"]
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return "[unsupported trace value]"

    safe = clean(value)
    encoded = stable_json(safe)
    if len(encoded) > max_characters:
        return {"content_truncated": True, "redacted_excerpt": encoded[:max_characters-200], "note": f"Sanitized span content capped at {max_characters} characters; text fields at {text_limit}."}
    return safe


class _NullSpan:
    trace_id = ""

    def set_inputs(self, _value):
        pass

    def set_outputs(self, _value):
        pass

    def set_attribute(self, _key, _value):
        pass

    def set_content_inputs(self, _value):
        pass

    def set_content_outputs(self, _value):
        pass

    def set_documents(self, _value):
        pass


class _MeasuredSpan:
    def __init__(self, live, tracer):
        self.live, self.tracer = live, tracer
        self.inputs, self.outputs = {}, {}

    def _call(self, name, *args):
        started = time.perf_counter()
        try:
            return getattr(self.live, name)(*args)
        except Exception as exc:
            self.tracer.error = type(exc).__name__
        finally:
            self.tracer.overhead_ms += (time.perf_counter() - started) * 1000

    def set_inputs(self, value):
        self.inputs.update(value)
        return self._call("set_inputs", self.inputs)

    def set_outputs(self, value):
        self.outputs.update(value)
        return self._call("set_outputs", self.outputs)

    def _content(self, value, direction):
        if self.tracer.content_mode != "redacted":
            return
        started = time.perf_counter()
        try:
            safe = trace_content(value, self.tracer.content_limit, self.tracer.text_limit)
        except Exception as exc:
            safe = {"content_unavailable": type(exc).__name__}
        self.tracer.overhead_ms += (time.perf_counter()-started)*1000
        return getattr(self, f"set_{direction}")(safe)

    def set_content_inputs(self, value):
        return self._content(value, "inputs")

    def set_content_outputs(self, value):
        return self._content(value, "outputs")

    def set_attribute(self, key, value):
        return self._call("set_attribute", key, value)

    def set_documents(self, value):
        # MLflow's retrieval scorers require a top-level list of page_content chunks.
        if self.tracer.content_mode == "redacted":
            return self._call("set_outputs", trace_content(value, self.tracer.content_limit, self.tracer.text_limit))


class MlflowTracer:
    """Best-effort exporter; local demos can opt into bounded redacted content."""

    def __init__(self, url: str, experiment: str, content_mode: str = "metadata", detail: str = "standard"):
        if content_mode not in {"metadata", "redacted"}:
            raise ValueError("Unknown MLflow trace content mode")
        self.content_mode = content_mode
        if detail not in {"standard", "deep"}:
            raise ValueError("Unknown MLflow trace detail")
        self.detail = detail
        self.experiment_id = ""
        self.destination_experiment_id = ""
        self.text_limit, self.content_limit = (32_000, 192_000) if detail == "deep" else (6000, 24_000)
        self.url = safe_service_url(url, "MLflow") if url else ""
        self.experiment = experiment
        self.enabled = bool(self.url)
        self.error = ""
        self.trace_id = ""
        self.setup_ms = 0.0
        self.overhead_ms = 0.0
        self._mlflow = None
        started = time.perf_counter()
        if self.enabled:
            try:
                import mlflow

                mlflow.set_tracking_uri(self.url)
                experiment = select_experiment(mlflow, self.experiment, self.url)
                self.experiment, self.experiment_id = experiment.name, experiment.experiment_id
                self._mlflow = mlflow
            except Exception as exc:  # tracing must never break the debugger path
                self.enabled = False
                self.error = type(exc).__name__
        self.setup_ms = (time.perf_counter() - started) * 1000

    @contextmanager
    def span(self, name: str, span_type: str = "CHAIN", inputs: dict | None = None, attributes: dict | None = None):
        if not self.enabled or self._mlflow is None:
            yield _NullSpan()
            return
        manager = None
        live = None
        try:
            started = time.perf_counter()
            destination = {}
            if self.destination_experiment_id:
                from mlflow.entities import MlflowExperimentLocation
                destination["trace_destination"] = MlflowExperimentLocation(self.destination_experiment_id)
            manager = self._mlflow.start_span(name=name, span_type=span_type, **destination)
            live = manager.__enter__()
            self.overhead_ms += (time.perf_counter() - started) * 1000
            measured = _MeasuredSpan(live, self)
            measured.set_attribute("kravel.content_mode", self.content_mode)
            measured.set_attribute("kravel.trace_detail", self.detail)
            measured.set_attribute("kravel.content_limit", self.content_limit)
            measured.set_attribute("kravel.text_limit", self.text_limit)
            if inputs is not None:
                measured.set_inputs(inputs)
            for key, value in (attributes or {}).items():
                measured.set_attribute(key, value)
            current_id = getattr(live, "trace_id", "") or getattr(live, "request_id", "")
            if current_id and not self.trace_id:
                self.trace_id = str(current_id)
        except Exception as exc:
            self.error = type(exc).__name__
            if manager is not None and live is not None:
                try:
                    manager.__exit__(None, None, None)
                except Exception:
                    pass
            yield _NullSpan()
            return
        try:
            yield measured
        except BaseException as exc:
            started = time.perf_counter()
            # Never export an unguarded exception message or raw stack locals.
            safe_error = RuntimeError(str(public_evidence(str(exc))) if self.content_mode == "redacted" else type(exc).__name__)
            try:
                manager.__exit__(RuntimeError, safe_error, None)
            except Exception as export_error:
                self.error = type(export_error).__name__
            self.overhead_ms += (time.perf_counter() - started) * 1000
            raise
        else:
            try:
                started = time.perf_counter()
                manager.__exit__(None, None, None)
                self.overhead_ms += (time.perf_counter() - started) * 1000
            except Exception as exc:
                self.error = type(exc).__name__

    def annotate_trace(self, *, session_id=None, tags=None, metadata=None):
        if self.enabled and self._mlflow is not None:
            try:
                self._mlflow.update_current_trace(session_id=session_id, tags=tags, metadata=metadata)
            except Exception as exc:
                self.error = type(exc).__name__

    def flush(self) -> float:
        if not self.enabled or self._mlflow is None:
            return 0.0
        started = time.perf_counter()
        try:
            self._mlflow.flush_trace_async_logging()
        except Exception as exc:
            self.error = type(exc).__name__
        return (time.perf_counter() - started) * 1000

    def set_previews(self, *, question=None, diagnosis=None):
        """Human-readable trace-list previews, only when content recording is enabled."""
        if not self.enabled or self._mlflow is None or self.content_mode != "redacted":
            return
        started = time.perf_counter()
        try:
            previews = {}
            for key, value in (("request_preview", question), ("response_preview", diagnosis)):
                if value is not None:
                    safe = trace_content(str(value))
                    previews[key] = safe[:950] + ("\n[preview truncated; inspect span fields]" if len(safe) > 950 else "")
            self._mlflow.update_current_trace(**previews)
        except Exception as exc:
            self.error = type(exc).__name__
        finally:
            self.overhead_ms += (time.perf_counter()-started)*1000
