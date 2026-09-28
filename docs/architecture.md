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
                         └─ context shard with explicit caveats
                                      │
                                      └─ bounded local tool loop ── hosted LLM
```

The context shard is an evidence package. It intentionally does not label a change as the root cause. That conclusion belongs to a rule engine, a model, or a human and should include confidence and counter-evidence.

## Agent harness

The hosted-LLM harness reuses the MCP tool implementations instead of giving the model direct database or Kubernetes access. The model can request rewind, diff, context, and graph-trace operations; Kravel validates and executes them locally. Cluster and namespace are removed from the model-visible schemas and fixed by the caller, so tool arguments cannot expand the investigation's authority scope. Each tool response has a character ceiling, each turn accepts at most four calls, and the loop has a hard turn limit. When that limit is reached, the harness disables further tool calls and requests a final uncertainty-qualified report.

The harness is intentionally read-only. It does not expose `kubectl`, a shell, admission controls, or remediation functions. Kubernetes fields returned by tools are treated as untrusted evidence because annotations and event messages can contain prompt-injection text.

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
