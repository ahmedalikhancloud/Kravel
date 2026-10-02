"""Isolated, serial MLflow evaluation worker. No Kubernetes imports or permissions."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import json
import os
import queue
import re
import sqlite3
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

from .evaluation_catalog import GUIDELINES, SESSION, builtins, catalog, skip_reason
from .judge_bridge import JudgeBridge
from .tracing import MlflowTracer, trace_content
from .utils import is_internal_hostname, safe_service_url, to_iso


def validate_request(body):
    if not isinstance(body, dict) or set(body) - {"traceId", "runId", "profile", "expectedFacts", "expectedResponse"}:
        raise ValueError("Only a recorded run, profile, and optional independent references are accepted")
    if not re.fullmatch(r"tr-[a-f0-9]{32}", str(body.get("traceId", ""))):
        raise ValueError("A valid recorded trace ID is required")
    if not re.fullmatch(r"[a-f0-9-]{36}", str(body.get("runId", ""))):
        raise ValueError("A valid completed investigation ID is required")
    if body.get("profile", "quick") not in {"quick", "all"}:
        raise ValueError("Evaluation profile must be quick or all")
    facts = body.get("expectedFacts", [])
    reference = body.get("expectedResponse", "")
    if not isinstance(facts, list) or len(facts) > 12 or any(not isinstance(f, str) or not f.strip() or len(f) > 1000 for f in facts):
        raise ValueError("Supply at most 12 nonempty reference facts of at most 1000 characters")
    if not isinstance(reference, str) or len(reference) > 6000:
        raise ValueError("Reference answer must be at most 6000 characters")
    return {"traceId": body["traceId"], "runId": body["runId"], "profile": body.get("profile", "quick"),
            "expectedFacts": trace_content(facts), "expectedResponse": trace_content(reference)}


def judge_view(trace):
    """Derived view: root question/answer only; original trace and span IDs stay intact.

    All inputs were already redacted by Kravel. Re-redact at this boundary, remove
    prior assessments (never treat previous judge answers as reference truth), and
    bound text for laptop inference. Original persisted traces are not rewritten.
    """
    from mlflow.entities import Trace
    raw = trace.to_dict()
    raw["info"]["assessments"] = []
    for span in raw["data"]["spans"]:
        attrs = span["attributes"]
        for key in ("mlflow.spanInputs", "mlflow.spanOutputs"):
            if key in attrs:
                attrs[key] = json.dumps(trace_content(json.loads(attrs[key]), 96_000, 6000))
        if not span.get("parent_span_id"):
            inputs = json.loads(attrs.get("mlflow.spanInputs", "{}"))
            outputs = json.loads(attrs.get("mlflow.spanOutputs", "{}"))
            if not isinstance(inputs, dict) or not isinstance(outputs, dict) or not inputs.get("question") or not outputs.get("diagnosis"):
                raise ValueError("Trace has no recorded question/answer; enable redacted content for new requests")
            attrs["mlflow.spanInputs"] = json.dumps({"question": inputs["question"]})
            attrs["mlflow.spanOutputs"] = json.dumps(outputs["diagnosis"])
    return Trace.from_dict(raw)


class EvaluationWorker:
    def __init__(self, *, tracking_url, model, model_url, db_path, source_experiment="Kravel Guarded Debugger",
                 experiment="Kravel Guarded Debugger", activity_url="", bridge=None):
        os.environ["MLFLOW_DISABLE_TELEMETRY"] = "true"
        import mlflow
        tracking_url = safe_service_url(tracking_url, "MLflow")
        if not is_internal_hostname(urlparse(tracking_url).hostname):
            raise ValueError("Evaluation tracking must remain local")
        self.mlflow, self.model = mlflow, model
        self.source_experiment, self.experiment = source_experiment, experiment
        self.activity_url = safe_service_url(activity_url, "debugger activity") if activity_url else ""
        if self.activity_url and not is_internal_hostname(urlparse(self.activity_url).hostname):
            raise ValueError("Activity checks must remain local")
        self.bridge = bridge or JudgeBridge(model, model_url)
        # Native MLflow gateway envs for pinned 3.16.0. No provider API key is needed.
        os.environ["OPENAI_API_KEY"] = "not-required"
        os.environ["OPENAI_API_BASE"] = self.bridge.url
        os.environ["OPENAI_BASE_URL"] = self.bridge.url
        os.environ["MLFLOW_GENAI_EVAL_MAX_SCORER_WORKERS"] = "1"
        os.environ["MLFLOW_GENAI_EVAL_MAX_WORKERS"] = "1"
        os.environ["MLFLOW_GENAI_EVAL_MAX_RETRIES"] = "0"
        os.environ["MLFLOW_JUDGE_MAX_ITERATIONS"] = "3"
        # SDK auto evaluator tracing can export raw exception stacks/arguments.
        # We instead create linked, manually sanitized EVALUATOR/LLM spans.
        os.environ["MLFLOW_GENAI_EVAL_ENABLE_SCORER_TRACING"] = "false"
        mlflow.set_tracking_uri(tracking_url)
        self.tracer = MlflowTracer(tracking_url, experiment, "redacted", "deep")
        if not self.tracer.enabled:
            raise RuntimeError("MLflow evaluation tracking is unavailable")
        if source_experiment == experiment:
            self.source_experiment = self.tracer.experiment
        self.experiment = self.tracer.experiment
        judges = mlflow.get_experiment_by_name("Kravel Local Judges")
        self.tracer.destination_experiment_id = judges.experiment_id if judges else mlflow.create_experiment("Kravel Local Judges")
        self.scorers = builtins(model)
        self.lock = threading.RLock()
        # MLflow's session-level scorer pool is independently parallel. This lock
        # serializes both pools and protects the one active local transport context.
        self.scorer_lock = threading.RLock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        for (raw,) in self.db.execute("SELECT payload FROM jobs").fetchall():
            job = json.loads(raw)
            if job["status"] in {"queued", "running"}:
                job.update(status="interrupted", error="Worker restarted; scores were not replayed or fabricated.")
                self.save(job)
        self.pending = queue.Queue(maxsize=4)
        self.active = threading.Event()

    def save(self, job):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?)", (job["id"], json.dumps(job)))
            self.db.commit()

    def get(self, job_id):
        with self.lock:
            row = self.db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def jobs(self, run_id=""):
        with self.lock:
            rows = self.db.execute("SELECT payload FROM jobs ORDER BY rowid DESC LIMIT 100").fetchall()
        return [j for row in rows if (j := json.loads(row[0])) and (not run_id or j["runId"] == run_id)]

    def catalog(self):
        # Resolve the effective experiment (including an HTTP-artifact migration)
        # with a read-only lookup. Never expose configured internal URLs or keys.
        source = self.mlflow.get_experiment_by_name(self.source_experiment)
        previews = []
        if source:
            try:
                # Metadata previews only: do not download historical logs/spans
                # or create a model call just to give the picker readable labels.
                traces = self.mlflow.search_traces(locations=[source.experiment_id],
                    filter_string="tags.`kravel.kind` = 'investigation'", max_results=30,
                    return_type="list", include_spans=False)
                for trace in traces:
                    run_id = trace.info.trace_metadata.get("kravel.run_id", "")
                    preview = trace.info.request_preview
                    if re.fullmatch(r"[a-f0-9-]{36}", run_id) and preview:
                        previews.append({"runId": run_id, "question": trace_content(preview, 2000, 400)})
            except Exception:
                pass  # Missing previews never invalidate recorded trace links.
        return {"scorers": catalog(self.model), "model": self.model, "advisoryOnly": True,
                "mlflowVersion": self.mlflow.__version__, "requestPreviews": previews,
                "experiments": {
                    "source": {"id": source.experiment_id if source else "", "name": self.source_experiment},
                    "evaluations": {"id": self.tracer.experiment_id, "name": self.experiment},
                    "judges": {"id": self.tracer.destination_experiment_id, "name": "Kravel Local Judges"}}}

    def enqueue(self, body):
        body = validate_request(body)
        with self.lock:
            existing = next((j for j in self.jobs(body["runId"]) if j["status"] in {"queued", "running"}), None)
            if existing:
                return existing
            job = {"id": str(uuid.uuid4()), **body, "status": "queued", "createdAt": to_iso(), "model": self.model,
                   "scorers": [], "advisoryOnly": True, "view": "redacted root question/answer; per-text cap 6000; up to four real session turns"}
            # Put and save atomically relative to the consumer's first read.
            self.pending.put_nowait(job["id"])
            self.save(job)
        return job

    def start(self):
        def consume():
            while True:
                job_id = self.pending.get()
                with self.lock:
                    job = self.get(job_id)
                self.active.set()
                try:
                    self.evaluate(job)
                except Exception as exc:
                    job.update(status="failed", error="Evaluation failed: " + type(exc).__name__, finishedAt=to_iso())
                    self.save(job)
                finally:
                    self.active.clear()
                    self.pending.task_done()
        threading.Thread(target=consume, daemon=True, name="serial-local-evaluation").start()

    def wait_for_karl(self):
        if not self.activity_url:
            return
        # Wait only between scorers. Shared inference cannot preempt an active judge.
        deadline = time.monotonic()+300
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self.activity_url + "/v1/activity", timeout=5) as response:
                    if not json.load(response).get("investigationActive"):
                        return
            except Exception:
                raise RuntimeError("Cannot confirm Karl is idle; evaluation paused safely") from None
            time.sleep(2)
        raise TimeoutError("Karl stayed busy for five minutes")

    def source_data(self, job):
        source = self.mlflow.get_trace(job["traceId"])
        experiment = self.mlflow.get_experiment_by_name(self.source_experiment)
        if (not experiment or source.info.experiment_id != experiment.experiment_id or
                source.info.tags.get("kravel.kind") != "investigation" or
                source.info.trace_metadata.get("kravel.run_id") != job["runId"]):
            raise ValueError("Evaluation only accepts Kravel investigation traces from the configured experiment")
        view = judge_view(source)
        session_id = source.info.trace_metadata.get("mlflow.trace.session")
        session = [view]
        if job["profile"] == "all" and session_id:
            # Session IDs are server-created UUIDs, not client-controlled filter expressions.
            if not re.fullmatch(r"[a-f0-9-]{36}", session_id):
                raise ValueError("Invalid recorded session ID")
            traces = self.mlflow.search_traces(locations=[experiment.experiment_id],
                filter_string=f"metadata.`mlflow.trace.session` = '{session_id}'", return_type="list", max_results=30)
            earlier = [t for t in traces if t.info.tags.get("kravel.kind") == "investigation" and t.info.timestamp_ms <= source.info.timestamp_ms]
            session = []
            for trace in sorted(earlier, key=lambda t: t.info.timestamp_ms)[-4:]:
                try:
                    session.append(judge_view(trace))
                except ValueError:
                    pass  # Content-free/failed turns cannot be fabricated into a conversation.
            if not any(t.info.trace_id == view.info.trace_id for t in session):
                session.append(view)
        return view, session

    def adapter(self, name, builtin, job, row):
        from mlflow.entities import AssessmentError, Feedback
        from mlflow.genai.scorers import scorer

        def run_one(arguments):
            started = time.perf_counter()
            row.update(status="running", startedAt=to_iso())
            self.save(job)
            feedbacks = []
            transport = {"calls": 0, "errors": []}
            self.tracer.trace_id = ""
            try:
                self.wait_for_karl()
                with self.tracer.span("evaluate." + name, "EVALUATOR", {"scorer": name, "source_trace_id": job["traceId"], "model": self.model}) as span:
                    self.tracer.annotate_trace(tags={"kravel.kind": "evaluation", "mlflow.trace.sourceScorer": builtin.name},
                        metadata={"kravel.job_id": job["id"], "kravel.source_trace_id": job["traceId"], "kravel.advisory_only": "true"})
                    try:
                        rubric = builtin.instructions
                    except (AttributeError, NotImplementedError):
                        rubric = builtin.description + " (composed native scorer; child requests show its actual rubrics)"
                    span.set_content_inputs({"rubric": rubric,
                        "inputs": arguments.get("inputs"), "outputs": arguments.get("outputs"), "expectations": arguments.get("expectations"),
                        "session_trace_ids": [t.info.trace_id for t in arguments.get("session", [])], "judge_view": job["view"]})
                    kwargs = {k: v for k, v in arguments.items() if k in inspect.signature(builtin.__call__).parameters}
                    if name in {"Correctness", "RetrievalSufficiency"} and (kwargs.get("expectations") or {}).get("expected_facts"):
                        # MLflow requires one factual reference source, not both.
                        kwargs["expectations"] = {k: v for k, v in kwargs["expectations"].items() if k != "expected_response"}
                    with self.bridge.activate(getattr(span, "live", None)) as transport:
                        result = builtin(**kwargs)
                    feedbacks = result if isinstance(result, list) else [result]
                    if not feedbacks:
                        raise ValueError("Native scorer returned no assessment")
                    for index, feedback in enumerate(feedbacks):
                        # Use the real native Feedback, but remove unsanitized exception text.
                        feedback.rationale = trace_content(feedback.rationale or "")
                        if feedback.error or transport.get("errors"):
                            # Some native helpers suppress failed tool extraction and
                            # continue judging. Never present such a partial fallback
                            # as an intact/valid score.
                            feedback = Feedback(name=feedback.name, source=feedback.source, span_id=feedback.span_id,
                                rationale=feedback.rationale, metadata=trace_content(feedback.metadata or {}),
                                error=AssessmentError("LOCAL_JUDGE_ERROR", "Native scorer did not produce a valid local score."))
                            feedbacks[index] = feedback
                        else:
                            feedback.value = trace_content(feedback.value)
                        feedback.metadata = {**trace_content(feedback.metadata or {}),
                            "mlflow.assessment.scorerTraceId": self.tracer.trace_id,
                            "kravel.job_id": job["id"], "kravel.judge_model": self.model,
                            "kravel.duration_ms": str(round((time.perf_counter()-started)*1000, 2)),
                            "kravel.advisory_only": "true"}
                    row.update(status="error" if any(f.error for f in feedbacks) else "completed",
                        feedback=[trace_content(f.to_dictionary(), 24_000, 6000) for f in feedbacks], traceId=self.tracer.trace_id,
                        modelCalls=transport.get("inferenceCalls", transport["calls"]), transportAttempts=transport["calls"])
                    if transport.get("errors"):
                        row["transportErrors"] = transport["errors"]
                    if transport.get("rejections"):
                        row["rejectionReasons"] = transport["rejections"]
                    span.set_content_outputs({"feedback": row["feedback"], "status": row["status"], "model_calls": row["modelCalls"], "transport_attempts": row["transportAttempts"], "rejection_reasons": row.get("rejectionReasons", [])})
            except Exception as exc:
                reasons = transport.get("rejections", [])
                row.update(status="error", error="Local scorer failed: " + type(exc).__name__ + ("; " + ", ".join(reasons) if reasons else ""),
                    traceId=self.tracer.trace_id, modelCalls=transport.get("inferenceCalls", transport["calls"]),
                    transportAttempts=transport["calls"], rejectionReasons=reasons)
                feedbacks = [Feedback(name=builtin.name, error=AssessmentError("LOCAL_JUDGE_ERROR", row["error"]),
                    metadata={"kravel.job_id": job["id"], "kravel.advisory_only": "true", "mlflow.assessment.scorerTraceId": self.tracer.trace_id})]
            finally:
                row["durationMs"] = (time.perf_counter()-started)*1000
                self.tracer.flush()
                self.save(job)
            return feedbacks

        def invoke(arguments):
            with self.scorer_lock:
                return run_one(arguments)

        if name in SESSION:
            def session_scorer(session, expectations=None):
                return invoke({"session": session, "expectations": expectations})
            return scorer(name=builtin.name)(session_scorer)
        def trace_scorer(inputs=None, outputs=None, expectations=None, trace=None):
            return invoke({"inputs": inputs, "outputs": outputs, "expectations": expectations, "trace": trace})
        return scorer(name=builtin.name)(trace_scorer)

    def evaluate(self, job):
        self.wait_for_karl()
        view, session = self.source_data(job)
        question = view.data.spans[0].inputs["question"]
        expectations = {"guidelines": GUIDELINES}
        if job["expectedFacts"]:
            expectations["expected_facts"] = job["expectedFacts"]
        if job["expectedResponse"]:
            expectations["expected_response"] = job["expectedResponse"]
        has_retrieval = any(s.span_type == "RETRIEVER" and isinstance(s.outputs, list) and s.outputs for s in view.data.spans)
        has_tools = any(s.span_type == "TOOL" for s in view.data.spans)
        session_has_tools = any(s.span_type == "TOOL" for t in session for s in t.data.spans)
        selected = []
        job.update(status="running", startedAt=to_iso(), sourceExperimentId=view.info.experiment_id,
                   sessionTraceIds=[t.info.trace_id for t in session], scorers=[])
        job["scorerExperimentId"] = self.tracer.destination_experiment_id
        for name, builtin in self.scorers.items():
            reason = skip_reason(name, profile=job["profile"], has_trace=True, has_retrieval=has_retrieval,
                has_tools=has_tools, session_has_tools=session_has_tools, turns=len(session), question=question, expectations=expectations)
            row = {"class": name, "name": builtin.name, "status": "skipped" if reason else "pending", "reason": reason}
            job["scorers"].append(row)
            if not reason:
                selected.append(self.adapter(name, builtin, job, row))
        self.save(job)
        # Trace-level scorers see only the selected answer. Session scorers see actual
        # preceding turns, not fake repetitions or other users' unrelated histories.
        rows = [{"trace": view, "inputs": {"question": question}, "outputs": view.data.spans[0].outputs, "expectations": expectations}]
        with self.mlflow.start_run(run_name="Local judges · " + job["profile"] + " · " + job["runId"][:8]) as run:
            job.update(mlflowRunId=run.info.run_id, experimentId=run.info.experiment_id)
            self.save(job)
            self.mlflow.set_tags({"kravel.kind": "evaluation", "kravel.source_trace_id": job["traceId"], "kravel.profile": job["profile"], "kravel.judge_model": self.model, "kravel.advisory_only": "true"})
            # Split native session evaluation from answer evaluation, in one MLflow run.
            trace_scorers = [s for s in selected if not s.is_session_level_scorer]
            session_scorers = [s for s in selected if s.is_session_level_scorer]
            if trace_scorers:
                result = self.mlflow.genai.evaluate(data=rows, scorers=trace_scorers)
                self.mlflow.log_dict(trace_content(result.result_df.to_dict(orient="records"), 192_000, 6000), "answer-results.json")
            if session_scorers:
                result = self.mlflow.genai.evaluate(data=session, scorers=session_scorers)
                self.mlflow.log_dict(trace_content(result.result_df.to_dict(orient="records"), 192_000, 6000), "session-results.json")
            job.update(status="completed_with_errors" if any(r["status"] == "error" for r in job["scorers"]) else "completed", finishedAt=to_iso())
            self.mlflow.log_dict(trace_content(job, 192_000, 6000), "kravel-evaluation-report.json")
            self.mlflow.log_metrics({"kravel.scorers_completed": sum(r["status"] == "completed" for r in job["scorers"]),
                "kravel.scorers_errors": sum(r["status"] == "error" for r in job["scorers"]), "kravel.scorers_skipped": sum(r["status"] == "skipped" for r in job["scorers"])})
        self.save(job)


def create_evaluation_server(worker, host="127.0.0.1", port=8083):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, payload):
            encoded = json.dumps(trace_content(payload, 192_000, 6000)).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/healthz":
                return self.send(200, {"status": "ok", "active": worker.active.is_set(), "kubernetesAccess": False})
            if path == "/v1/catalog":
                try:
                    return self.send(200, worker.catalog())
                except Exception:
                    return self.send(503, {"error": "Tracking catalog unavailable; reconnect local MLflow and retry."})
            if path.startswith("/v1/jobs/"):
                job = worker.get(path.split("/")[-1])
                return self.send(200 if job else 404, job or {"error": "not_found"})
            if path == "/v1/jobs":
                from urllib.parse import parse_qs
                run_id = parse_qs(urlparse(self.path).query).get("runId", [""])[-1]
                return self.send(200, {"jobs": worker.jobs(run_id)[:10]})
            return self.send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/v1/jobs":
                return self.send(404, {"error": "not_found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 24_000:
                    raise ValueError("Bounded evaluation request required")
                return self.send(202, worker.enqueue(json.loads(self.rfile.read(length))))
            except queue.Full:
                return self.send(429, {"error": "Evaluation queue is full; wait for a job to finish."})
            except Exception as exc:
                return self.send(400, {"error": "Invalid evaluation request: " + type(exc).__name__})
    return ThreadingHTTPServer((host, port), Handler)


def main():
    worker = EvaluationWorker(tracking_url=os.environ["KRAVEL_MLFLOW_URL"],
        model=os.getenv("KRAVEL_JUDGE_MODEL", "ai/qwen3:4b-instruct-2507-q4_K_M"),
        model_url=os.getenv("KRAVEL_JUDGE_BASE_URL", "http://model-runner.docker.internal/engines/v1"),
        db_path=os.getenv("KRAVEL_EVALUATION_DB", "/data/evaluations.db"),
        activity_url=os.getenv("KRAVEL_DEBUGGER_URL", ""))
    worker.start()
    create_evaluation_server(worker, os.getenv("KRAVEL_HOST", "127.0.0.1"), int(os.getenv("KRAVEL_PORT", "8083"))).serve_forever()


if __name__ == "__main__":
    main()
