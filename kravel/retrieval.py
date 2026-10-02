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

from .guardrails import guard_model_input
from .scenarios import catalog, catalog_hash, VERSION


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
    key = (str(directory.resolve()), directory.stat().st_mtime_ns, cross)
    with _LOCK:
        if key not in _MODELS:
            cls = CrossEncoder if cross else SentenceTransformer
            _MODELS[key] = cls(str(directory), device="cpu", local_files_only=True, trust_remote_code=False)
        return _MODELS[key]


def cached_vectors(cases, encoder, model_key, cache_path):
    key = hashlib.sha256((catalog_hash() + model_key).encode()).hexdigest()
    if not cache_path:
        return encoder([document(c) for c in cases])
    with sqlite3.connect(cache_path, timeout=5) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS runbook_vectors (cache_key TEXT PRIMARY KEY, vectors TEXT NOT NULL)")
        row = connection.execute("SELECT vectors FROM runbook_vectors WHERE cache_key=?", (key,)).fetchone()
        if row:
            return json.loads(row[0])
        vectors = encoder([document(c) for c in cases])
        # Cache only curated runbook vectors, never operator questions/logs/credentials.
        connection.execute("DELETE FROM runbook_vectors")
        connection.execute("INSERT INTO runbook_vectors VALUES (?,?)", (key, json.dumps(vectors, allow_nan=False)))
        return vectors


def retrieve(query, *, tracer=None, limit=4, encoder=None, reranker=None, cache_path=""):
    started = time.perf_counter()
    query = guard_model_input(query, "runbook retrieval", 6000)["value"]
    cases = catalog()
    timings, unavailable = {}, []

    def stage(name, call):
        tick = time.perf_counter()
        context = tracer.span("rag." + name, "RETRIEVER", {"catalog_version": VERSION}) if tracer else nullcontext(None)
        with context as span:
            result = call()
            if span:
                span.set_outputs({"candidate_count": len(result)})
        timings[name + "Ms"] = (time.perf_counter()-tick)*1000
        return result

    lexical = stage("bm25", lambda: bm25(query, cases))[:12]
    rankings = [lexical]
    embedding_path = os.getenv("KRAVEL_RAG_EMBEDDING_PATH", "")
    if embedding_path or encoder:
        try:
            if encoder is None:
                model = local_model(embedding_path)
                encoder = lambda texts: model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()
            # A directory timestamp is an operator-controlled model-cache revision.
            model_key = embedding_path + (str(Path(embedding_path).stat().st_mtime_ns) if embedding_path else "test")
            def dense():
                vectors = cached_vectors(cases, encoder, model_key, cache_path)
                if len(vectors) != len(cases):
                    raise ValueError("Embedding document count does not match catalog")
                qvector = encoder([query])[0]
                return sorted([(c["id"], cosine(qvector, v)) for c, v in zip(cases, vectors)], key=lambda row: (-row[1], row[0]))[:12]
            rankings.append(stage("dense", dense))
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
                scores = reranker([(query, document(indexed[sid])) for sid, _ in candidates])
                if len(scores) != len(candidates) or not all(math.isfinite(float(s)) for s in scores):
                    raise ValueError("Invalid cross-encoder scores")
                return sorted([(row[0], float(score)) for row, score in zip(candidates, scores)], key=lambda row: (-row[1], row[0]))
            candidates = stage("cross_encoder", rerank)
        except Exception as exc:
            unavailable.append({"stage": "cross_encoder", "errorType": type(exc).__name__})
    hits = [{**indexed[sid], "retrievalScore": score} for sid, score in candidates[:max(1, min(int(limit), 6))]]
    result = {"hits": hits, "mode": "hybrid_rrf" if len(rankings) > 1 else "bm25",
        "reranked": "cross_encoderMs" in timings, "timings": timings, "unavailable": unavailable,
        "version": VERSION, "catalogHash": catalog_hash(), "totalMs": (time.perf_counter()-started)*1000,
        "notice": "Runbook relevance is not a proven root cause or permission to execute a fix."}
    if tracer:
        with tracer.span("rag.context", "RETRIEVER", {"mode": result["mode"], "reranked": result["reranked"]}) as span:
            span.set_content_inputs({"sanitized_query": query})
            span.set_outputs({k: v for k, v in result.items() if k != "hits"})
            span.set_documents([{"page_content": document(c), "metadata": {"scenario_id": c["id"], "source": c["source"], "catalog_version": VERSION, "execution_mode": c["executionMode"]}} for c in hits])
    return result
