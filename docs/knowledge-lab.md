# Karl’s knowledge lab: free local hybrid RAG

Knowledge is a clue, not a diagnosis or execution permission. Karl retrieves
runbooks, then compares their evidence requirements with live Kubernetes facts.
All mutations still require the independent approval broker.

## Enable once

Open Git Bash in the repository and keep the existing demo connected:

```bash
bash demo/local/demo.sh --connect-only
bash demo/local/enable-rag.sh
bash demo/local/demo.sh --connect-only
```

The enable script builds/warms the retrieval service and refreshes only Kravel’s
debugger. It refuses active investigations/approvals; it does not reset labs,
delete history, rotate keys, or change your application workloads. The last
command reconnects localhost browser links after the rollout. No Groq,
Codespaces, cloud account, HF token, hosted vector database or paid router.
Downloads require internet and disk space; runtime inference does not.

## A five-minute presentation

1. Open **Karl’s knowledge lab** below the practice cards. The four green stages
   mean keyword search, vector search, fusion, and reranking are enabled.
2. Click **Restart mystery**, or search `my container repeatedly starts and dies
   due to bad startup command`. Expand **Why these references?** to see each
   stage’s top three rankings. Expand a result to read its matched passage and
   evidence contract. Scores are relevance scores, not confidence probabilities.
3. Click **Stale knowledge** and **Model delivery**. The six new support/model
   delivery references are invented examples—not employer or customer documents.
   They are knowledge only; they do not create services or simulate live incidents.
4. Choose **platform support** in **Knowledge collection**. Both BM25 and vector
   search now operate on that collection *before* candidate fusion. This is
   search scoping only, not an authenticated tenant boundary.
5. Open **Compare retrieval** and click **Run the local retrieval comparison**.
   It runs 26 authored questions on the same corpus with four stage variants.
   No Qwen calls or Kubernetes changes happen. Results persist across restarts.
6. Use **All retrieval spans** to open the exact MLflow trace. Inspect
   `rag.bm25`, `rag.dense`, `rag.rrf`, `rag.cross_encoder`, `rag.selection`, and
   `rag.context`. Each includes candidate IDs/ranks/scores, sanitized questions,
   selected reference text, collection metadata and actual compute spans.
7. Open **Per-query results & model provenance** for the MLflow metrics and
   JSON artifacts. A worse result is useful evidence, not something hidden.
8. For the end-to-end demo, use an existing practice lab and ask Karl to
   investigate. The request trace links its separate retrieval service trace;
   the investigation’s `rag.context` also records the documents passed onward.
   Follow live diagnostics, exact plan preview, independent approval and execution.

## What is being compared?

| Variant | Purpose | Measured quality |
|---|---|---|
| BM25 | Exact words and error codes | Recall@3, MRR@3, nDCG@3 |
| Dense | Similar meaning using MiniLM embeddings | Same labels and corpus |
| RRF | Combine positions, not incompatible raw scores | Same labels and corpus |
| Reranked | Cross-encoder evaluates query/passage pairs | Same candidate shortlist |

The dataset is a small authored synthetic regression set, **not held-out production
data**, not a general benchmark, and not proof of root-cause or repair accuracy.
Latency columns are warm-stage compute costs summed from **one pipeline’s**
stages; they exclude HTTP, cold-start and tracing overhead. Individual MLflow
spans show actual stage runtime. No extra LLM judge is needed for labeled retrieval
metrics. Existing answer-level MLflow LLM-as-a-Judge scoring remains separate.

## Local architecture and provenance

The optional `kravel-knowledge` service has no Kubernetes token or execution tools.
It serializes CPU inference with two Torch threads and a 1.5 GiB memory limit;
Qwen retains its existing GPU runner. Long custom references use overlapping
850-character passages; vector results are aggregated per runbook before fusion.
BM25/vector pools are bounded to 12 each; reranking evaluates up to eight fused
runbooks. Only curated document vectors enter SQLite—not operator questions,
cluster snapshots, logs or credentials. Cache keys include the actual corpus and
model artifact manifest, including hashes/revision.

Models and licenses:

- [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2),
  Apache-2.0, revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`.
- [ms-marco-MiniLM-L6-v2](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2),
  Apache-2.0, revision `233902d25c440f23af6f7d6e94d2946bac0bee0a`.

The build downloads JSON/tokenizer/safetensors artifacts only; no remote Python
code or pickle weights. Startup verifies the file hashes and fails readiness if
either required stage cannot warm up. The main worker exposes honest BM25 fallback
when the knowledge service is missing, busy or fails. Local redacted MLflow
content is best-effort privacy protection, not a guarantee: inspect before sharing.

## Add your own *non-sensitive* knowledge

`KRAVEL_RUNBOOKS_PATH` accepts a bounded read-only JSON file, up to 200 additional
runbooks / 1 MiB. Each document needs `id`, `title`, `category`, `signals`,
`evidenceRequired`, `remediation`, `verification`, and an allowed official `source`.
Optional `collection` is a lowercase slug. Fields are bounded and guarded; runbooks
cannot declare executable authority. Mount the same reviewed JSON and set its path
in both debugger and knowledge-service deployments so their catalogs agree.
The next search uses new corpus/model cache keys; no vector DB migration.

Do not paste resume contents, customer documents, PHI, credentials or confidential
runbooks. This demo has no OIDC-derived entitlements, multi-tenant authorization,
durable work queue or production-grade data governance. A collection dropdown
is not a substitute for those capabilities.

## Disable or recover without deleting data

Finish active investigations, then disable the remote service for Karl:

```bash
kubectl -n kravel-system set env deployment/kravel KRAVEL_RAG_URL=
kubectl -n kravel-system rollout status deployment/kravel --timeout=4m
bash demo/local/demo.sh --connect-only
```

Re-enable with `bash demo/local/enable-rag.sh`. To inspect startup trouble:

```bash
kubectl -n kravel-observability get pods -l app=kravel-knowledge
kubectl -n kravel-observability logs deployment/kravel-knowledge --tail=100
kubectl -n kravel-observability describe pods -l app=kravel-knowledge
```

The knowledge PVC and MLflow history remain intact. Interrupted benchmarks are
marked interrupted at restart; they never silently resume a cluster action.
