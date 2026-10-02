"""Serial CPU-only knowledge service. No Kubernetes identity or execution tools."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlparse

from .rag_benchmark import VERSION as DATASET_VERSION, dataset, dataset_hash, score, summarize
from .retrieval import retrieve
from .scenarios import catalog, catalog_hash, VERSION
from .tracing import MlflowTracer, trace_content
from .utils import to_iso


class KnowledgeWorker:
    def __init__(self, db_path, search=retrieve, warm=True):
        self.db_path, self.search = db_path, search
        self.slot = threading.Lock()
        self.ready = False
        self.latest = ""
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS rag_jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            for jid, raw in list(db.execute("SELECT id,payload FROM rag_jobs ORDER BY rowid")):
                job = json.loads(raw)
                if job["status"] in {"queued", "running"}:
                    job.update(status="interrupted", error="Service restarted; nothing was executed in Kubernetes.")
                    self.save(job)
                self.latest = jid
        if warm:
            from .rag_artifacts import verify
            for name in ("KRAVEL_RAG_EMBEDDING_PATH", "KRAVEL_RAG_RERANKER_PATH"):
                verify(os.environ[name])
            result = self.search("Kubernetes image pull troubleshooting", remote=False, cache_path=self.cache)
            if result["mode"] != "hybrid_rrf" or not result["reranked"] or result["unavailable"]:
                raise RuntimeError("Required local retrieval models failed warm-up: " + json.dumps(result["unavailable"]))
        self.ready = True

    @property
    def cache(self):
        return str(Path(self.db_path).parent / "runbook-vectors.db")

    def connect(self):
        return sqlite3.connect(self.db_path, timeout=10)

    def save(self, job):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO rag_jobs VALUES (?,?)", (job["id"], json.dumps(trace_content(job, 1_800_000, 6000))))

    def job(self, jid):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM rag_jobs WHERE id=?", (jid,)).fetchone()
        return json.loads(row[0]) if row else None

    def tracer(self):
        return MlflowTracer(os.getenv("KRAVEL_MLFLOW_URL", ""), "Kravel Knowledge Lab", "redacted", "deep")

    def status(self):
        cases = catalog()
        models = {}
        for role, env in (("embedding", "KRAVEL_RAG_EMBEDDING_PATH"), ("reranker", "KRAVEL_RAG_RERANKER_PATH")):
            path = Path(os.getenv(env, "/nonexistent")) / "kravel-artifact.json"
            spec = json.loads(path.read_text()) if path.is_file() else {}
            models[role] = {key: spec.get(key, "") for key in ("repo", "revision", "license")}
            models[role]["artifactFiles"] = len(spec.get("sha256", {}))
        return {"ready": self.ready, "busy": self.slot.locked(), "mode": "hybrid_rrf", "reranked": True,
            "device": "CPU", "runtimeOffline": os.getenv("HF_HUB_OFFLINE") == "1", "models": models,
            "documentCount": len(cases), "catalogHash": catalog_hash(), "version": VERSION,
            "collections": sorted({c.get("collection", "kubernetes") for c in cases}),
            "datasetVersion": DATASET_VERSION, "benchmarkQueries": len(dataset(cases)), "latestJobId": self.latest,
            "notice": "Collection filtering is search scoping, not tenant authorization. All bundled examples are public/synthetic."}

    def query(self, body):
        if not isinstance(body, dict) or set(body) - {"query", "limit", "collection"} or not isinstance(body.get("query"), str) or not 1 <= len(body["query"]) <= 6000:
            raise ValueError("Supply a bounded query, optional collection and limit")
        if type(body.get("limit", 6)) is not int or not 1 <= body.get("limit", 6) <= 6:
            raise ValueError("Limit must be an integer from 1 to 6")
        if not self.slot.acquire(timeout=2):
            raise RuntimeError("Knowledge benchmark/search is busy; lexical fallback remains available")
        try:
            tracer = self.tracer()
            with tracer.span("rag.search", "CHAIN") as root:
                root.set_content_inputs(body)
                result = self.search(body["query"], collection=body.get("collection", "all"), limit=body.get("limit", 6), tracer=tracer, remote=False, cache_path=self.cache)
                root.set_outputs({"mode": result["mode"], "selected_ids": [h["id"] for h in result["hits"]]})
                tracer.set_previews(question=body["query"], diagnosis="References: " + ", ".join(h["title"] for h in result["hits"]))
            tracer.flush()
            result.update(traceId=tracer.trace_id, experimentId=tracer.experiment_id, traceError=tracer.error)
            return result
        finally:
            self.slot.release()

    def start_benchmark(self, body):
        if body != {}:
            raise ValueError("Benchmark accepts only the bundled synthetic dataset, not uploaded private data")
        if not self.slot.acquire(blocking=False):
            raise RuntimeError("Knowledge worker is busy; try again after the current request")
        job = {"id": str(uuid.uuid4()), "status": "queued", "createdAt": to_iso(), "completedQueries": 0,
            "datasetVersion": DATASET_VERSION, "notice": "Authored synthetic regression set, not held-out production accuracy. No LLM calls or cluster changes."}
        self.latest = job["id"]
        try:
            self.save(job)
            threading.Thread(target=self.benchmark, args=(job,), daemon=True).start()
        except Exception:
            self.slot.release()
            raise
        return job

    def benchmark(self, job):
        try:
            rows = dataset(catalog())
            tracer = self.tracer()
            mlflow = tracer._mlflow
            job.update(status="running", queryCount=len(rows), datasetHash=dataset_hash(rows), catalogHash=catalog_hash(), rows=[])
            self.save(job)
            with tracer.span("rag.benchmark", "CHAIN", {"dataset_version": DATASET_VERSION, "query_count": len(rows)}) as root:
                for item in rows:
                    with tracer.span("rag.benchmark.query", "CHAIN") as span:
                        span.set_content_inputs(item)
                        result = self.search(item["query"], limit=6, tracer=tracer, remote=False, cache_path=self.cache)
                        if result["mode"] != "hybrid_rrf" or not result["reranked"] or result["unavailable"]:
                            raise RuntimeError("Incomplete hybrid stages; refusing to report a complete ablation")
                        timing = result["timings"]
                        # Warm-stage compute costs from the SAME candidate run, not
                        # four independent end-to-end wall-clock measurements.
                        latencies = {"bm25": timing["bm25Ms"], "dense": timing["denseMs"],
                            "rrf": timing["bm25Ms"]+timing["denseMs"]+timing["rrfMs"],
                            "reranked": sum(timing.values())}
                        variants = {name: {**score([r["id"] for r in ranking], item["relevantIds"]), "latencyMs": latencies[name], "topIds": [r["id"] for r in ranking[:3]]}
                            for name, ranking in result["rankings"].items() if ranking}
                        row = {**item, "variants": variants, "timings": timing}
                        job["rows"].append(row)
                        span.set_outputs(row)
                        job["completedQueries"] += 1
                        self.save(job)
                job.update(summary=summarize(job["rows"]), traceId=tracer.trace_id, experimentId=tracer.experiment_id,
                    latencyMethod="Warm-stage compute costs summed from one candidate pipeline; excludes HTTP, cold start and tracing overhead.")
                root.set_outputs(job["summary"])
                tracer.set_previews(question="Compare local BM25, dense, RRF and cross-encoder", diagnosis=json.dumps(job["summary"]))
            tracer.flush()
            if mlflow:
                with mlflow.start_run(run_name="Retrieval ablation · " + job["id"][:8], experiment_id=tracer.experiment_id) as run:
                    job["mlflowRunId"] = run.info.run_id
                    mlflow.set_tags({"kravel.kind": "retrieval-benchmark", "kravel.dataset": DATASET_VERSION,
                        "kravel.synthetic": "true", "kravel.retrieval_trace_id": tracer.trace_id})
                    mlflow.log_params({"catalog_hash": job["catalogHash"], "dataset_hash": job["datasetHash"], "query_count": len(rows), "device": "cpu"})
                    mlflow.log_metrics({name + "." + key: value for name, values in job["summary"].items() for key, value in values.items()})
                    mlflow.log_dict({"dataset": rows, "results": job["rows"], "summary": job["summary"], "notice": job["notice"], "latencyMethod": job["latencyMethod"]}, "retrieval-ablation.json")
                    mlflow.log_dict(self.status()["models"], "model-provenance.json")
            job.update(status="completed", finishedAt=to_iso(), traceError=tracer.error)
        except Exception as exc:
            job.update(status="failed", error=type(exc).__name__, finishedAt=to_iso())
        finally:
            try:
                self.save(job)
            finally:
                self.slot.release()


def create_server(worker, host="0.0.0.0", port=8084):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def send(self, status, value):
            raw = json.dumps(trace_content(value, 1_800_000, 6000)).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/healthz": return self.send(200 if worker.ready else 503, {"ready": worker.ready})
            if path == "/v1/status": return self.send(200, worker.status())
            if re.fullmatch(r"/v1/jobs/[a-f0-9-]{36}", path):
                job = worker.job(path.split("/")[-1])
                return self.send(200 if job else 404, job or {"error": "not_found"})
            return self.send(404, {"error": "not_found"})

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 32_000: raise ValueError("Oversized knowledge request")
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/v1/search": return self.send(200, worker.query(body))
                if self.path == "/v1/benchmark": return self.send(202, worker.start_benchmark(body))
                return self.send(404, {"error": "not_found"})
            except RuntimeError as exc:
                return self.send(409, {"error": str(exc)})
            except Exception as exc:
                return self.send(400, {"error": type(exc).__name__})
    return ThreadingHTTPServer((host, port), Handler)


def main():
    import torch
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    worker = KnowledgeWorker(os.getenv("KRAVEL_RAG_DB", "/data/knowledge.db"))
    create_server(worker).serve_forever()


if __name__ == "__main__": main()
