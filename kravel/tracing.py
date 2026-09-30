from __future__ import annotations

from contextlib import contextmanager
import time

from .utils import safe_service_url


class _NullSpan:
    trace_id = ""

    def set_inputs(self, _value):
        pass

    def set_outputs(self, _value):
        pass

    def set_attribute(self, _key, _value):
        pass


class _MeasuredSpan:
    def __init__(self, live, tracer):
        self.live, self.tracer = live, tracer

    def _call(self, name, *args):
        started = time.perf_counter()
        try:
            return getattr(self.live, name)(*args)
        finally:
            self.tracer.overhead_ms += (time.perf_counter() - started) * 1000

    def set_inputs(self, value):
        return self._call("set_inputs", value)

    def set_outputs(self, value):
        return self._call("set_outputs", value)

    def set_attribute(self, key, value):
        return self._call("set_attribute", key, value)


class MlflowTracer:
    """Best-effort MLflow trace exporter with metadata-only span payloads."""

    def __init__(self, url: str, experiment: str):
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
                mlflow.set_experiment(self.experiment)
                self._mlflow = mlflow
            except Exception as exc:  # tracing must never break the debugger path
                self.enabled = False
                self.error = str(exc)
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
            manager = self._mlflow.start_span(name=name, span_type=span_type)
            live = manager.__enter__()
            self.overhead_ms += (time.perf_counter() - started) * 1000
            measured = _MeasuredSpan(live, self)
            if inputs is not None:
                measured.set_inputs(inputs)
            for key, value in (attributes or {}).items():
                measured.set_attribute(key, value)
            current_id = getattr(live, "trace_id", "") or getattr(live, "request_id", "")
            if current_id and not self.trace_id:
                self.trace_id = str(current_id)
        except Exception as exc:
            self.error = str(exc)
            yield _NullSpan()
            return
        try:
            yield measured
        except BaseException as exc:
            started = time.perf_counter()
            manager.__exit__(type(exc), exc, exc.__traceback__)
            self.overhead_ms += (time.perf_counter() - started) * 1000
            raise
        else:
            try:
                started = time.perf_counter()
                manager.__exit__(None, None, None)
                self.overhead_ms += (time.perf_counter() - started) * 1000
            except Exception as exc:
                self.error = str(exc)

    def flush(self) -> float:
        if not self.enabled or self._mlflow is None:
            return 0.0
        started = time.perf_counter()
        try:
            self._mlflow.flush_trace_async_logging()
        except Exception as exc:
            self.error = str(exc)
        return (time.perf_counter() - started) * 1000
