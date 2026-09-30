# Architecture and production path

## Data model

Kravel separates four kinds of truth:

1. **Object versions** are authoritative for reconstruction. Every list/watch event stores a sanitized full object and the patch from its predecessor. A deletion stores a tombstone.
2. **Audit events** establish the API actor, verb, source address, response, and a stronger request timeline. They do not overwrite object state because audit policy may omit bodies.
3. **Kubernetes Events** describe symptoms such as scheduling failure, eviction, and crash backoff. They are lossy and may be aggregated.
4. **Metric samples** capture numeric behavior around the same clock window.

Each object version has both `observed_at` and `event_at`. The former is when Kravel received it. The latter is the best source timestamp available, falling back to receipt time. Rewind orders by `event_at` and insertion id.

Full sanitized snapshots make the MVP easy to verify. Stored patches make explanations compact. At scale, a checkpoint plus ordered-patch scheme can reduce storage while preserving deterministic reconstruction.

## Query flow

```text
incident timestamp
      │
      ├─ stateAt(t) ── ordered replay ── exact objects ── relationship graph
      │
      ├─ stateAt(t-window) + stateAt(t) ── deterministic JSON diff
      │
      └─ bounded evidence query
             ├─ changes (+ optional vector similarity)
             ├─ audit identity
             ├─ Kubernetes Events
             └─ metrics
                         │
                         └─ incident shards with explicit caveats
                                      │
                                      ├─ deterministic Kubernetes signals
                                      ├─ guarded Laya classification of ambiguous shards
                                      └─ policy-gated, guarded Qwen tool loop
```

Each incident shard is a small, independently explainable evidence package. It intentionally distinguishes a deterministic Kubernetes diagnosis from a temporal hypothesis: an image-pull warning paired with an image change can be classified without a model, while a nearby ConfigMap edit remains a hypothesis until stronger causal evidence exists.

## Guarded incident pipeline

Kravel first partitions evidence into focused incident shards. Native Kubernetes signals handle mechanically provable cases such as image-pull failures, selector drift, and failed scheduling. Only ambiguous shards are sent through input guardrails to the local Laya zero-shot classifier. A deterministic policy gate uses the combined class probabilities, confidence, top-versus-runner-up margin, and a severe-class allow-list. Routine, high-confidence incidents execute a bounded read-only runbook that repeats the temporal diff and correlates matching Events. Ambiguous or severe incidents escalate to the local Qwen reasoning model.

The Kubernetes watcher inherits `apiVersion` and `kind` from each enclosing list response when individual list items omit TypeMeta, as Kubernetes API servers are permitted to do. The demo waits until Kravel can reconstruct each annotated mutation before closing the incident window, so model latency cannot race collector ingestion.

The Python LangGraph harness reuses the MCP tool implementations instead of giving Qwen direct database or Kubernetes access. Cluster and namespace scope are fixed by the caller, so tool arguments cannot expand the investigation's authority. For the fast local profile, LangGraph deterministically selects a bounded state diff plus incident context, executes them as traced read-only tool nodes, applies the Qwen input guardrail, and makes one Qwen synthesis request. Tool output is capped at 3.5 KB and the report at 520 tokens. The slower Thinking profile is retained as an opt-in comparison.

MLflow receives a real trace rather than a collection of nominal tracking runs. `kravel.incident_pipeline` is the root span; LangGraph stages, guardrails, classifiers, tool executions, and Qwen synthesis are nested beneath it. Only bounded operational metadata is attached to spans.

Laya and Qwen each have separately timed input and output guardrails. Stage timing is written to SQLite, exported to Prometheus, and logged to MLflow without storing evidence, prompts, or generated reports. Every route produces a proposal with `remediationExecuted: false` and `awaiting_human_review`; the routine proposal separately records `diagnosticAutomationExecuted: true`.

The harness is intentionally read-only. It does not expose `kubectl`, a shell, admission controls, or remediation functions. Kubernetes fields returned by tools are treated as untrusted evidence because annotations and event messages can contain prompt-injection text.

## Temporal cockpit and Karl

The local web cockpit is served by the same authenticated Kravel API process. It uses explicit, packaged static assets and makes no third-party browser requests. The topology view is derived from a reconstructed `stateAt(t)` result, not from the live Kubernetes API, so moving the clock changes every resource card and relationship consistently. Selecting a resource opens its reconstructed manifest, inferred relations, and changes from the selected baseline.

Karl is a presentation and investigation layer over the same temporal APIs. Greetings, warning summaries, resource explanations, and rewind operations are deterministic and grounded in stored evidence. A free-form root-cause request becomes an approval card; only explicit approval starts the guarded Laya-to-policy-to-Qwen pipeline. That pipeline remains read-only and produces a proposal for human review. Approval authorizes model investigation, never cluster remediation.

The demo writes a small `kravel-demo-window` ConfigMap containing only the scenario and its baseline/incident timestamps. The cockpit recognizes that marker and opens directly on the captured incident window. No model credentials, prompts, or generated reports are exposed in the page source.

## Watch correctness

For each collection path the collector:

1. Lists the collection and stores all visible objects.
2. Starts a watch from the collection `resourceVersion`.
3. Requests watch bookmarks but does not rely on their frequency.
4. Reconnects at the latest observed resource version.
5. On `410 Gone`, performs a new list and resumes from the new version.

This follows the Kubernetes list-then-watch model. It prevents gaps within the guarantees of the API server, but it cannot recover history that expired before Kravel observed it.

## “Timeline shards” at scale

The current SQLite file is one local shard. A production implementation should partition by `(cluster_id, UTC day)` and maintain:

- an append-only event log (Kafka/Redpanda, NATS JetStream, or a cloud equivalent);
- object-version storage in PostgreSQL/TimescaleDB, ClickHouse, or an object-store table format;
- periodic materialized cluster checkpoints;
- a vector index keyed by immutable change id;
- a graph projection derived from checkpoint state rather than an independent source of truth.

A practical retrieval plan is:

1. Hard-filter by cluster, time window, namespace, and graph neighborhood.
2. Retrieve lexical and vector candidates only inside that boundary.
3. Rank with time distance, object kind, actor, topology distance, and semantic similarity.
4. Always attach the source ids and timestamps presented to the model.

## Next milestones

### 0.2 — forensic fidelity

- Correlate audit and watch entries by UID, resource version, actor, and bounded time.
- Store previous container termination details and selected log references.
- Add clock-skew detection and source watermarks.
- Add checkpointing and property-based replay tests.

### 0.3 — production storage

- Pluggable event log and PostgreSQL/ClickHouse backend.
- Per-cluster/day partitions, compaction, retention, and legal hold.
- High-availability collectors with leader election and idempotency keys.

### 0.4 — causal investigation

- Topology-aware candidate expansion.
- Deployment/change-system adapters for Git SHA, CI run, and rollout identity.
- Hypotheses with confidence, supporting evidence, contradicting evidence, and a “not enough data” outcome.
- Evaluation fixtures with known incident causes and temporal leakage tests.
