import json
from pathlib import Path
import sqlite3
import time
import urllib.request

import pytest

from kravel.rag_artifacts import verify
from kravel.rag_benchmark import dataset, score, summarize
from kravel.rag_service import KnowledgeWorker, create_server
from kravel.retrieval import retrieve, passages, service_request
from kravel.scenarios import catalog
from test_api import serving, post


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    for key in ("KRAVEL_RAG_URL", "KRAVEL_RAG_EMBEDDING_PATH", "KRAVEL_RAG_RERANKER_PATH", "KRAVEL_RUNBOOKS_PATH", "KRAVEL_SYNTHETIC_RUNBOOKS", "KRAVEL_MLFLOW_URL"):
        monkeypatch.delenv(key, raising=False)


def test_synthetic_pack_is_optional_and_never_grants_writes(monkeypatch):
    assert len(catalog()) == 50
    monkeypatch.setenv("KRAVEL_SYNTHETIC_RUNBOOKS", "1")
    cases = catalog()
    assert len(cases) == 56
    extra = [c for c in cases if c.get("synthetic")]
    assert len(extra) == 6 and all(not c["restorableFields"] and c["executionMode"] == "operator_led" for c in extra)
    assert len(dataset(cases)) == 26


def test_collection_is_prefiltered_for_both_rankers_and_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("KRAVEL_SYNTHETIC_RUNBOOKS", "1")
    sizes = []
    def encode(texts):
        sizes.append(len(texts)); return [[1., 0.] for _ in texts]
    path = str(tmp_path / "vectors.db")
    result = retrieve("timeout", encoder=encode, collection="platform-support", cache_path=path)
    assert result["documentCount"] == 4
    assert all(h["collection"] == "platform-support" for h in result["hits"])
    other = retrieve("timeout", encoder=encode, collection="model-delivery", cache_path=path)
    assert other["documentCount"] == 2 and other["catalogHash"] != result["catalogHash"]
    assert len(sqlite3.connect(path).execute("SELECT * FROM runbook_vectors").fetchall()) == 2
    assert retrieve("timeout", collection="not-a-collection")["hits"] == []
    with pytest.raises(ValueError): retrieve("timeout", collection="../private")


def test_rank_provenance_and_scores_are_not_confidence():
    result = retrieve("image unknown tag", encoder=lambda texts: [[1., 0.] for _ in texts], reranker=lambda pairs: [float(i) for i, _ in enumerate(pairs)])
    assert result["mode"] == "hybrid_rrf" and result["reranked"]
    assert all(h["provenance"] and h["matchedPassage"] for h in result["hits"])
    assert set(result["rankings"]) == {"bm25", "dense", "rrf", "reranked"}
    assert "confidence" not in result


def test_native_retrieval_scorers_see_documents_not_stage_rank_dicts():
    from contextlib import contextmanager
    class Trace:
        def __init__(self): self.documents = []
        @contextmanager
        def span(self, name, kind, *args):
            self.kind = kind
            yield self
        def set_content_inputs(self, _): pass
        def set_outputs(self, _): assert self.kind != "RETRIEVER"
        def set_documents(self, value):
            assert self.kind == "RETRIEVER"
            self.documents.extend(value)
    tracer = Trace()
    retrieve("OOMKilled", tracer=tracer)
    assert tracer.documents and all("page_content" in d for d in tracer.documents)


def test_long_runbooks_have_overlapping_bounded_passages():
    case = {key: "word " * 400 for key in ("title", "signals", "evidenceRequired", "remediation", "verification")}
    chunks = passages(case)
    assert len(chunks) > 1 and all(len(c) <= 850 for c in chunks)
    assert chunks[0][-100:] == chunks[1][:100]


def test_service_url_cannot_send_queries_to_an_external_or_credentialed_host(monkeypatch):
    for url in ("https://example.com", "http://localhost:8084?token=private", "http://user:pass@localhost:8084"):
        monkeypatch.setenv("KRAVEL_RAG_URL", url)
        with pytest.raises(ValueError): service_request("/v1/status")


def test_unavailable_remote_has_explicit_lexical_fallback(monkeypatch):
    monkeypatch.setenv("KRAVEL_RAG_URL", "http://127.0.0.1:1")
    result = retrieve("OOMKilled")
    assert result["mode"] == "bm25" and not result["reranked"]
    assert result["unavailable"][0]["stage"] == "service"


def test_artifact_checksum_and_path_traversal(tmp_path):
    import hashlib
    (tmp_path / "weights").write_bytes(b"synthetic weights")
    manifest = {"sha256": {"weights": hashlib.sha256(b"synthetic weights").hexdigest()}}
    (tmp_path / "kravel-artifact.json").write_text(json.dumps(manifest))
    assert verify(tmp_path) == manifest
    (tmp_path / "weights").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"): verify(tmp_path)
    manifest["sha256"] = {"../outside": "hash"}
    (tmp_path / "kravel-artifact.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError): verify(tmp_path)


def test_metrics_definition_and_summary():
    good = score(["a", "b", "c"], ["b"])
    assert good["recallAt3"] == 1 and good["mrrAt3"] == .5
    assert score(["a"], ["b"])["ndcgAt3"] == 0
    assert summarize([{"variants": {"bm25": {**good, "latencyMs": 2}}}])["bm25"]["queryCount"] == 1


def test_remote_service_search_trace_and_synthetic_benchmark(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAVEL_SYNTHETIC_RUNBOOKS", "1")
    def search(query, **kw):
        kw.pop("remote", None)
        return retrieve(query, remote=False, encoder=lambda texts: [[1., 0.] for _ in texts], reranker=lambda pairs: [float(len(p)) for _, p in pairs], **kw)
    worker = KnowledgeWorker(str(tmp_path / "knowledge.db"), search=search, warm=False)
    with serving(create_server(worker, "127.0.0.1", 0)) as base:
        monkeypatch.setenv("KRAVEL_RAG_URL", base)
        result = retrieve("memory exceeded OOMKilled")
        assert result["mode"] == "hybrid_rrf" and "clientTotalMs" in result
        assert post(base, "/v1/search", {"query": "x", "shell": "kubectl delete"})[0] == 400
        assert post(base, "/v1/benchmark", {"dataset": "private"})[0] == 400
        status, job = post(base, "/v1/benchmark", {})
        assert status == 202
        for _ in range(100):
            saved = worker.job(job["id"])
            if saved["status"] not in {"running", "queued"}: break
            time.sleep(.02)
        assert saved["status"] == "completed" and saved["completedQueries"] == 26
        assert all(v["queryCount"] == 26 for v in saved["summary"].values())


def test_benchmark_does_not_claim_success_when_dense_is_missing(tmp_path):
    worker = KnowledgeWorker(str(tmp_path / "knowledge.db"), warm=False)
    job = worker.start_benchmark({})
    for _ in range(100):
        saved = worker.job(job["id"])
        if saved["status"] not in {"running", "queued"}: break
        time.sleep(.01)
    assert saved["status"] == "failed" and not worker.slot.locked()


def test_restart_marks_unfinished_jobs_interrupted(tmp_path):
    worker = KnowledgeWorker(str(tmp_path / "knowledge.db"), warm=False)
    worker.save({"id": "old", "status": "running"})
    new = KnowledgeWorker(worker.db_path, warm=False)
    assert new.job("old")["status"] == "interrupted"


def test_public_api_rag_status_fallback_and_rejects_unreviewed_dataset():
    from kravel.api import create_server as api_server
    from kravel.config import load_config
    from kravel.store import AuditStore
    config = load_config(); config.host, config.port = "127.0.0.1", 0
    with serving(api_server(AuditStore(), config, object())) as base:
        with urllib.request.urlopen(base + "/v1/rag/status") as response:
            status = json.load(response)
        assert not status["ready"] and status["mode"] == "bm25"
        assert post(base, "/v1/rag/benchmark", {"dataset": "private"})[0] == 400
