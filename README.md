# Kravel

Kravel is a time-travel memory service for Kubernetes agents. It records object versions from the Kubernetes watch API, correlates them with audit actors, Kubernetes Events, and Prometheus samples, and gives an AI agent deterministic tools to reconstruct what the cluster looked like before an incident.

> Status: working MVP. It is suitable for local evaluation and small test clusters, not yet for production incident forensics.

## What is implemented

- Exact `rewind_cluster_state(timestamp)` reconstruction from immutable, ordered object versions.
- `diff_states(t1, t2)` with RFC 6902-shaped changes.
- A resource graph for ownership, Service selectors, ConfigMaps, Secrets, volumes, and service accounts.
- Context shards that align object changes, audit identity, Kubernetes Events, and Prometheus samples.
- Optional OpenAI-compatible embeddings for evidence ranking. Embeddings never determine reconstructed state.
- A dependency-free HTTP API and MCP stdio server.
- A dependency-free hosted-LLM harness with bounded local tool execution (Groq by default).
- A measured Groq-versus-Laya comparison path with Prometheus, Grafana, and optional MLflow experiment logging.
- An in-cluster list/watch collector with `resourceVersion`, bookmarks, reconnects, and `410 Gone` recovery.
- Secret `data` / `stringData` redaction and removal of `managedFields` before persistence.

```text
 Kubernetes list/watch ─────── authoritative object versions ──┐
 Audit webhook/log adapter ── actor + verb + request metadata ─┤
 events.k8s.io watch ──────── symptoms and lifecycle events ───┼─> temporal store
 Prometheus instant queries ─ time-aligned numeric signals ─────┘        │
                                                                        ├─ rewind
 optional embedding service ─ relevance only ──────────────────────────┼─ diff
                                                                        ├─ graph trace
                                                                        └─ context shard -> LLM harness
                                                                                              │
                                                            hosted model <─ tool loop ─────────┘
```

The core design is deliberate: vector similarity is useful for finding evidence, but it is not a database consistency mechanism. Kravel reconstructs state from ordered snapshots and uses embeddings only to rank the evidence shown to a model.

## Try it

Node.js 24 or newer is the only local requirement.

```bash
node --test
node examples/demo.mjs
```

Run the API:

```bash
node src/main.mjs serve
```

In another terminal, ingest a version and rewind to it:

```bash
curl -X POST http://127.0.0.1:8080/v1/ingest/resource \
  -H 'content-type: application/json' \
  -d '{"clusterId":"demo","action":"ADDED","eventAt":"2026-09-28T12:00:00Z","object":{"apiVersion":"v1","kind":"ConfigMap","metadata":{"namespace":"shop","name":"api-config","resourceVersion":"1"},"data":{"TIMEOUT":"30s"}}}'

curl 'http://127.0.0.1:8080/v1/state/rewind?clusterId=demo&timestamp=2026-09-28T12:01:00Z'
```

Data is stored in `data/kravel.db` by default.

## Agent tools (MCP)

Start the newline-delimited JSON-RPC stdio server with:

```bash
node src/main.mjs mcp
```

It serves both MCP `2026-07-28` stateless discovery and the `2025-11-25` handshake lifecycle, and exposes:

- `rewind_cluster_state`
- `diff_states`
- `get_incident_context`
- `trace_resource`

An MCP host can launch it with this shape (replace the path with the absolute path on that machine):

```json
{
  "mcpServers": {
    "kravel": {
      "command": "node",
      "args": ["/absolute/path/to/kravel/src/main.mjs", "mcp"],
      "env": {
        "KRAVEL_DB_PATH": "/var/lib/kravel/kravel.db",
        "KRAVEL_CLUSTER_ID": "production"
      }
    }
  }
}
```

## Hosted-LLM agent

Kravel also includes a complete local tool-calling harness. The hosted model chooses among the same four read-only temporal tools; Kravel executes each call against SQLite, bounds the result, and sends the evidence back for a final incident report. No model SDK is required.

The default provider is Groq's OpenAI-compatible endpoint with `openai/gpt-oss-20b`:

```bash
read -rsp 'Groq API key: ' GROQ_API_KEY && echo
export GROQ_API_KEY
npm run agent -- \
  --baseline 2026-09-28T12:00:00Z \
  --incident 2026-09-28T12:05:00Z \
  --namespace shop
unset GROQ_API_KEY
```

The agent is evidence-only: it has no Kubernetes mutation tools and cannot apply changes. Tool results are sanitized at ingestion and capped before they are sent to the provider. Cluster evidence still leaves the cluster, so use the synthetic demo or review your provider's data policy before using real workloads.

Any compatible Chat Completions endpoint with function calling can be selected with `KRAVEL_LLM_BASE_URL`, `KRAVEL_LLM_MODEL`, and `KRAVEL_LLM_API_KEY`.

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/ingest/resource` | Ingest an authoritative object version |
| `POST` | `/v1/ingest/audit` | Receive one audit Event, an EventList, or an array |
| `POST` | `/v1/ingest/kubernetes-event` | Ingest a Kubernetes Event |
| `POST` | `/v1/ingest/metric` | Ingest one numeric metric sample |
| `GET` | `/v1/state/rewind` | Reconstruct state at `timestamp` |
| `GET` | `/v1/state/diff` | Compare `from` and `to` |
| `GET` | `/v1/state/graph` | Reconstruct state plus resource relationships |
| `GET` | `/v1/state/trace` | Trace neighbors around `resourceKey` |
| `GET` | `/v1/context` | Build incident evidence around `incidentAt` |
| `GET` | `/metrics` | Prometheus metrics for recorded flow comparisons |
| `POST` | `/v1/admin/prune` | Apply configured retention |
| `GET` | `/healthz`, `/readyz` | Health and storage counts |

Use RFC 3339 timestamps. `lookback` accepts values such as `30s`, `15m`, `2h`, or seconds. Resource keys have the form `apiVersion|Kind|namespace|name`, for example `v1|ConfigMap|shop|api-config`.

If `KRAVEL_API_TOKEN` is set, all `/v1/*` calls require `Authorization: Bearer <token>`. Health endpoints remain unauthenticated.

## Run in Kubernetes

For the complete copy-and-paste presentation flow, use the [demo runbook](DEMO.md). The deeper [Killercoda walkthrough](docs/killercoda.md) explains the individual scenarios and implementation details. The demo builds this repository inside a disposable Kubernetes playground and can run either a focused ConfigMap failure or a four-incident production-style sequence covering a crash loop, silent Service selector drift, a bad image rollout, and failed scheduling—no local cluster or registry required.

The optional [flow-comparison walkthrough](docs/observability-comparison.md) adds browser-accessible dashboards and compares the hosted Groq agent with a Laya System-1 classifier running in a separate GitHub Codespace.

1. Build and publish the image, then replace `ghcr.io/YOUR_ORG/kravel:0.1.0` in [`deploy/kubernetes.yaml`](deploy/kubernetes.yaml).
2. Review the ClusterRole. The default manifest intentionally does not grant access to Secret objects.
3. Apply the manifest:

   ```bash
   kubectl apply -f deploy/kubernetes.yaml
   ```

4. Set `KRAVEL_PROMETHEUS_URL` if Prometheus is reachable from the Pod.
5. Configure the API server audit webhook only on clusters where you control that setting. See [`docs/audit-ingestion.md`](docs/audit-ingestion.md).

The deployment uses one replica because the MVP uses SQLite. A persistent volume is required for durable history.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `KRAVEL_DB_PATH` | `./data/kravel.db` | SQLite database path |
| `KRAVEL_CLUSTER_ID` | host name | Stable cluster identity |
| `KRAVEL_HOST` / `KRAVEL_PORT` | `127.0.0.1` / `8080` | API bind address |
| `KRAVEL_API_TOKEN` | empty | Optional bearer token |
| `KRAVEL_RETENTION_DAYS` | `30` | Retention used by prune endpoint |
| `KRAVEL_WATCH_RESOURCES` | core workload list | JSON array of Kubernetes collection paths |
| `KRAVEL_PROMETHEUS_URL` | empty | Prometheus base URL |
| `KRAVEL_PROMETHEUS_QUERIES` | three pod signals | JSON object of signal name to PromQL |
| `KRAVEL_EMBEDDING_URL` | empty | OpenAI-compatible embeddings endpoint |
| `KRAVEL_EMBEDDING_MODEL` | empty | Model sent to that endpoint |
| `KRAVEL_EMBEDDING_API_KEY` | empty | Optional endpoint credential |
| `KRAVEL_LLM_BASE_URL` | Groq OpenAI-compatible API | Hosted Chat Completions base URL |
| `KRAVEL_LLM_MODEL` | `openai/gpt-oss-20b` | Tool-capable hosted model |
| `KRAVEL_LLM_API_KEY` / `GROQ_API_KEY` | empty | Hosted model credential |
| `KRAVEL_LLM_MAX_TURNS` | `6` | Maximum tool-calling turns, capped at 10 |

The comparison CLI accepts the Laya URL and both runtime credentials over standard input. They are intentionally not part of persistent configuration.

## Important limits

- Kravel cannot reconstruct time before it first listed an object.
- A watch receipt timestamp is an observation time, not proof of when a user initiated a change. Audit timestamps provide the stronger clock when available.
- Kubernetes Events are best-effort, may be aggregated, and are treated as evidence rather than authoritative state.
- A high relevance score means “worth showing the investigator,” not “caused the incident.”
- SQLite performs an ordered replay for reconstruction. Production scale needs checkpoints, partitioning, and a replicated event store.
- The watcher currently uses an in-cluster service account. Out-of-cluster kubeconfig support is not implemented.

See [`docs/architecture.md`](docs/architecture.md) for the production path and [`docs/security.md`](docs/security.md) before connecting a real cluster.

## Primary references

- [Kubernetes API change detection and watch semantics](https://kubernetes.io/docs/reference/using-api/api-concepts/)
- [Kubernetes audit configuration API](https://kubernetes.io/docs/reference/config-api/apiserver-audit.v1/)
- [Kubernetes Event API](https://kubernetes.io/docs/reference/kubernetes-api/events/event-v1/)
- [Prometheus HTTP query API](https://prometheus.io/docs/prometheus/latest/querying/api/)

## License

Apache-2.0. See [`LICENSE`](LICENSE).
