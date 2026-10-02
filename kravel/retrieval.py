"""Local runbook retrieval. No web ingestion, cluster writes, or permission grants."""
from __future__ import annotations

from collections import Counter
from contextlib import nullcontext
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import urllib.request
from urllib.parse import urlparse

from .guardrails import guard_model_input
from .scenarios import catalog, catalog_hash, VERSION
from .utils import is_internal_hostname, safe_service_url


def tokens(text):
    # Preserve exact error codes, and split camel-case so human paraphrases match.
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(text))
    return re.findall(r"[a-z0-9]+", (str(text) + " " + words).lower())


def document(case):
    return " ".join(str(case[key]) for key in ("title", "signals", "evidenceRequired", "remediation", "verification"))


def bm25(query, cases):
    terms = set(tokens(query))
    docs = [Counter(tokens(document(case))) for case in cases]
    lengths = [sum(doc.values()) for doc in docs]
    average = sum(lengths) / max(len(lengths), 1) or 1
    frequencies = Counter(term for doc in docs for term in doc)
    scored = []
    for case, counts, length in zip(cases, docs, lengths):
        score = 0.0
        for term in terms:
            tf = counts[term]
            idf = math.log(1 + (len(docs) - frequencies[term] + .5) / (frequencies[term] + .5))
            score += idf * tf * 2.5 / (tf + 1.5 * (.25 + .75 * length / average))
        if score > 0:
            scored.append((case["id"], score))
    return sorted(scored, key=lambda row: (-row[1], row[0]))


def rrf(rankings, constant=60):
    scores = Counter()
    for ranking in rankings:
        for rank, (sid, _) in enumerate(ranking, 1):
            scores[sid] += 1 / (constant + rank)
    return sorted(scores.items(), key=lambda row: (-row[1], row[0]))


def cosine(left, right):
    if len(left) != len(right) or not left:
        raise ValueError("Embedding dimensions do not match")
    norm = math.sqrt(sum(x*x for x in left) * sum(x*x for x in right))
    value = sum(a*b for a, b in zip(left, right)) / norm if norm else 0
    if not math.isfinite(value):
        raise ValueError("Non-finite embedding score")
    return value


_MODELS, _LOCK = {}, threading.RLock()


def local_model(path, cross=False):
    directory = Path(path)
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError("Retrieval models must be existing absolute local directories")
    # Loading local models must never initiate a hub download or execute repo code.
    from sentence_transformers import CrossEncoder, SentenceTransformer
    manifest = directory / "kravel-artifact.json"
    revision = hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else str(directory.stat().st_mtime_ns)
    key = (str(directory.resolve()), revision, cross)
    with _LOCK:
        if key not in _MODELS:
            cls = CrossEncoder if cross else SentenceTransformer
            _MODELS[key] = cls(str(directory), device="cpu", local_files_only=True, trust_remote_code=False, model_kwargs={"use_safetensors": True})
        return _MODELS[key]


def passages(case):
    text = document(case)
    # Bounded overlapping passages prevent long custom documents from silently
    # losing their final sections to the embedding model's token truncation.
    return [text[i:i+850] for i in range(0, max(1, len(text)-100), 750)]


def cached_vectors(cases, encoder, model_key, cache_path):
    texts = [p for c in cases for p in passages(c)]
    key = hashlib.sha256((json.dumps(texts, ensure_ascii=False) + model_key).encode()).hexdigest()
    if not cache_path:
        return encoder(texts)
    with sqlite3.connect(cache_path, timeout=5) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS runbook_vectors (cache_key TEXT PRIMARY KEY, vectors TEXT NOT NULL)")
        row = connection.execute("SELECT vectors FROM runbook_vectors WHERE cache_key=?", (key,)).fetchone()
        if row:
            return json.loads(row[0])
        vectors = encoder(texts)
        # Cache only curated runbook vectors, never operator questions/logs/credentials.
        connection.execute("DELETE FROM runbook_vectors WHERE rowid NOT IN (SELECT rowid FROM runbook_vectors ORDER BY rowid DESC LIMIT 7)")
        connection.execute("INSERT INTO runbook_vectors VALUES (?,?)", (key, json.dumps(vectors, allow_nan=False)))
        return vectors


def service_request(path, *, body=None, timeout=20):
    endpoint = os.getenv("KRAVEL_RAG_URL", "")
    if not endpoint:
        raise ValueError("Hybrid retrieval is not enabled; run bash demo/local/enable-rag.sh")
    endpoint = safe_service_url(endpoint, "retrieval service")
    parsed = urlparse(endpoint)
    if not is_internal_hostname(parsed.hostname) or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Retrieval must use a credential-free local service URL")
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(endpoint + path, data=data, headers={"Content-Type": "application/json"}), timeout=timeout) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Oversized retrieval response")
        return json.loads(raw)


def retrieve(query, *, tracer=None, limit=4, encoder=None, reranker=None, cache_path="", collection="all", remote=True):
    started = time.perf_counter()
    query = guard_model_input(query, "runbook retrieval", 6000)["value"]
    if not isinstance(collection, str) or not re.fullmatch(r"[a-z0-9_-]{1,60}", collection):
        raise ValueError("Invalid knowledge collection")
    remote_failure = []
    if remote and os.getenv("KRAVEL_RAG_URL") and encoder is None and reranker is None:
        try:
            context = tracer.span("rag.remote", "CHAIN", {"collection": collection}) if tracer else nullcontext(None)
            with context as span:
                result = service_request("/v1/search", body={"query": query, "limit": limit, "collection": collection})
                if span:
                    span.set_outputs({"retrieval_trace_id": result.get("traceId", ""), "timings": result["timings"], "mode": result["mode"]})
                if tracer:
                    record_context(tracer, query, result)
                result["clientTotalMs"] = (time.perf_counter()-started)*1000
                return result
        except Exception as exc:
            remote_failure.append({"stage": "service", "errorType": type(exc).__name__})
    all_cases = catalog()
    cases = [c for c in all_cases if collection == "all" or c.get("collection", "kubernetes") == collection]
    timings, unavailable = {}, []
    unavailable.extend(remote_failure)
    fingerprint = hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest()

    def stage(name, call):
        tick = time.perf_counter()
        # Only the final document context is a RETRIEVER span. Native MLflow
        # retrieval scorers expect its outputs to be page_content documents,
        # not numeric candidate/rank dictionaries from intermediate transforms.
        context = tracer.span("rag." + name, "CHAIN", {"catalog_version": VERSION}) if tracer else nullcontext(None)
        with context as span:
            if span:
                span.set_content_inputs({"sanitized_query": query, "collection": collection, "document_count": len(cases)})
            result = call()
            if span:
                span.set_outputs({"candidate_count": len(result), "ranking": [{"id": sid, "rank": rank, "score": score} for rank, (sid, score) in enumerate(result[:12], 1)]})
        timings[name + "Ms"] = (time.perf_counter()-tick)*1000
        return result

    lexical = stage("bm25", lambda: bm25(query, cases))[:12]
    dense_ranking = []
    excerpts = {}
    rankings = [lexical]
    embedding_path = os.getenv("KRAVEL_RAG_EMBEDDING_PATH", "")
    if cases and (embedding_path or encoder):
        try:
            if encoder is None:
                model = local_model(embedding_path)
                encoder = lambda texts: model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()
            manifest = Path(embedding_path) / "kravel-artifact.json" if embedding_path else None
            model_key = manifest.read_text() if manifest and manifest.is_file() else embedding_path + (str(Path(embedding_path).stat().st_mtime_ns) if embedding_path else "test")
            def dense():
                vectors = cached_vectors(cases, encoder, model_key, cache_path)
                chunks = [(c["id"], p) for c in cases for p in passages(c)]
                if len(vectors) != len(chunks):
                    raise ValueError("Embedding document count does not match catalog")
                qvector = encoder([query])[0]
                scores = {}
                for (sid, passage), vector in zip(chunks, vectors):
                    score = cosine(qvector, vector)
                    if sid not in scores or score > scores[sid]:
                        scores[sid], excerpts[sid] = score, passage
                return sorted(scores.items(), key=lambda row: (-row[1], row[0]))[:12]
            dense_ranking = stage("dense", dense)
            rankings.append(dense_ranking)
        except Exception as exc:
            unavailable.append({"stage": "dense", "errorType": type(exc).__name__})
    fused = stage("rrf", lambda: rrf(rankings)) if len(rankings) > 1 else lexical
    indexed = {c["id"]: c for c in cases}
    candidates = fused[:8]
    reranker_path = os.getenv("KRAVEL_RAG_RERANKER_PATH", "")
    if candidates and (reranker_path or reranker):
        try:
            if reranker is None:
                model = local_model(reranker_path, cross=True)
                reranker = lambda pairs: model.predict(pairs, show_progress_bar=False).tolist()
            def rerank():
                scores = reranker([(query, excerpts.get(sid, document(indexed[sid])[:850])) for sid, _ in candidates])
                if len(scores) != len(candidates) or not all(math.isfinite(float(s)) for s in scores):
                    raise ValueError("Invalid cross-encoder scores")
                return sorted([(row[0], float(score)) for row, score in zip(candidates, scores)], key=lambda row: (-row[1], row[0]))
            candidates = stage("cross_encoder", rerank)
        except Exception as exc:
            unavailable.append({"stage": "cross_encoder", "errorType": type(exc).__name__})
    rank_maps = {name: {sid: {"rank": rank, "score": score} for rank, (sid, score) in enumerate(rows, 1)} for name, rows in (("bm25", lexical), ("dense", dense_ranking), ("rrf", fused))}
    hits = [{**indexed[sid], "retrievalScore": score, "provenance": {name: rows.get(sid) for name, rows in rank_maps.items()}, "matchedPassage": excerpts.get(sid, document(indexed[sid])[:850])} for sid, score in candidates[:max(1, min(int(limit), 6))]]
    result = {"hits": hits, "mode": "hybrid_rrf" if len(rankings) > 1 else "bm25",
        "reranked": "cross_encoderMs" in timings, "timings": timings, "unavailable": unavailable,
        "version": VERSION, "catalogHash": fingerprint, "collection": collection, "documentCount": len(cases),
        "rankings": {name: [{"id": sid, "title": indexed[sid]["title"], "score": score} for sid, score in rows] for name, rows in (("bm25", lexical), ("dense", dense_ranking), ("rrf", fused if len(rankings) > 1 else []), ("reranked", candidates if "cross_encoderMs" in timings else []))},
        "totalMs": (time.perf_counter()-started)*1000,
        "notice": "Runbook relevance is not a proven root cause or permission to execute a fix."}
    if tracer:
        record_context(tracer, query, result)
    return result


def record_context(tracer, query, result):
    with tracer.span("rag.selection", "CHAIN", {"mode": result["mode"], "reranked": result["reranked"]}) as span:
        span.set_content_inputs({"sanitized_query": query, "collection": result.get("collection", "all")})
        span.set_outputs({k: v for k, v in result.items() if k != "hits"})
    with tracer.span("rag.context", "RETRIEVER") as span:
        span.set_content_inputs({"sanitized_query": query})
        span.set_documents([{"page_content": document(c), "metadata": {"scenario_id": c["id"], "source": c["source"], "catalog_version": VERSION, "collection": c.get("collection"), "synthetic": c.get("synthetic", False), "execution_mode": c["executionMode"], "provenance": c.get("provenance", {})}} for c in result["hits"]])
