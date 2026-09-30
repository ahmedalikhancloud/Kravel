# Kravel

Kravel is an experimental, read-only Kubernetes incident agent with temporal memory. It continuously records Kubernetes object changes and Events so an investigation can reconstruct what the cluster looked like before a failure instead of inspecting only the current state.

The included demo runs entirely on one Windows laptop. Docker Desktop provides Kubernetes and local model inference; no hosted model, API key, public endpoint, or external playground is required.

## Incident pipeline

```text
Kubernetes watch + Events
          │
          ▼
Temporal reconstruction
          │
          ▼
Laya input guardrail ──► Laya System-1 classifier ──► Laya output guardrail
                                                        │
                                                        ▼
                                                   Policy gate
                                      ┌─────────────────┴─────────────────┐
                                      │                                   │
                           routine + high confidence             ambiguous or severe
                                      │                                   │
                         predefined read-only automation    Qwen input guardrail
                                                                          │
                                                               Qwen temporal tool loop
                                                                          │
                                                              Qwen output guardrail
                                      └─────────────────┬─────────────────┘
                                                        ▼
                                             Awaiting human review
```

On the routine path, Kravel executes a bounded read-only diagnostic runbook (temporal diff plus event correlation). It never executes remediation: both paths end with a structured proposal marked `awaiting_human_review` and `remediationExecuted: false`.

## What is measured

Each run records the following stage latencies in SQLite, Prometheus, Grafana, and MLflow:

- temporal evidence reconstruction;
- Laya input guardrail;
- Laya inference;
- Laya output guardrail;
- policy routing;
- predefined read-only automation when the routine route is selected;
- Qwen input guardrail, including every temporal tool result;
- Qwen model inference;
- Qwen temporal-tool execution;
- Qwen output guardrail;
- human-review package generation;
- MLflow logging overhead;
- observed end-to-end latency.

Prompts, evidence payloads, generated reports, object names, endpoint URLs, and credentials are intentionally excluded from MLflow and Prometheus.

## Local components

| Component | Location | Purpose |
|---|---|---|
| Kubernetes | Docker Desktop, one node | Runs the synthetic workloads and Kravel services |
| Kravel | `kravel-system` | Watches the cluster and stores temporal history |
| Laya | `kravel-ai`, CPU | Cheap initial classification |
| Qwen3 4B Thinking | Docker Model Runner, GPU | Deep tool-calling investigation |
| Prometheus and Grafana | `kravel-observability` | Aggregate and visualize pipeline latency |
| MLflow | `kravel-observability` | Inspect each run and its child stages |

## Quick start

Complete the one-time Docker Desktop setup in [DEMO.md](DEMO.md), then run:

```powershell
.\demo\local\prepare.cmd
.\demo\local\demo.cmd -Scenario escalation
```

Open:

- Grafana: `http://localhost:3000`
- MLflow: `http://localhost:5000`

Repeat the same reconstructed incident window to build latency distributions:

```powershell
.\demo\local\run-pipeline.cmd -Runs 3
```

Clean up disposable resources while keeping models cached:

```powershell
.\demo\local\reset.cmd
```

## Temporal interfaces

Kravel exposes four read-only tools through its internal harness and MCP server:

- `rewind_cluster_state(timestamp)`
- `diff_states(from, to)`
- `get_incident_context(incident_at, lookback)`
- `trace_resource(timestamp, resource_key)`

The HTTP API provides equivalent endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/v1/state/rewind` | Reconstruct object state at a timestamp |
| `GET` | `/v1/state/diff` | Diff two reconstructed snapshots |
| `GET` | `/v1/state/graph` | Build a topology graph at a timestamp |
| `GET` | `/v1/state/trace` | Trace a resource through that graph |
| `GET` | `/v1/context` | Build a bounded incident context shard |
| `GET` | `/metrics` | Export pipeline and stage metrics |

## Guardrails

Input guardrails normalize text, remove control characters, redact likely credentials, quarantine prompt-injection-like lines, and enforce size limits. Laya output is schema-validated so every expected probability is finite and between zero and one. Qwen output is redacted, bounded, checked for timestamps and uncertainty language, and has direct mutation commands withheld.

Guardrails reduce risk; they are not a security boundary. Kubernetes fields and Events remain attacker-controlled input. See [security notes](docs/security.md).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `KRAVEL_DB_PATH` | `data/kravel.db` | SQLite path |
| `KRAVEL_CLUSTER_ID` | hostname | Stable cluster identity |
| `KRAVEL_LAYA_URL` | internal Laya Service | System-1 endpoint |
| `KRAVEL_LAYA_MODEL` | `english` | Laya checkpoint |
| `KRAVEL_LLM_BASE_URL` | Docker Model Runner | OpenAI-compatible local endpoint |
| `KRAVEL_LLM_MODEL` | `ai/qwen3:4b-thinking-2507-q4_K_M` | Local reasoning model |
| `KRAVEL_LLM_MAX_TURNS` | `6` | Qwen tool-loop limit |
| `KRAVEL_MLFLOW_URL` | internal MLflow Service | Tracking endpoint |
| `KRAVEL_POLICY_HIGH_CONFIDENCE` | `0.85` | Routine-route confidence threshold |
| `KRAVEL_POLICY_MINIMUM_MARGIN` | `0.20` | Required top-versus-runner-up margin |
| `KRAVEL_POLICY_POSITIVE_THRESHOLD` | `0.65` | Independent positive-class threshold |

## Development

Kravel uses Node.js 24 and has no npm runtime dependencies:

```powershell
node --test
node examples/demo.mjs
```

The Laya container is pinned separately in [laya.Dockerfile](demo/local/laya.Dockerfile).

## Status

This is an incident-analysis prototype, not an autonomous remediation system. The demo uses synthetic workloads, ephemeral telemetry, zero-shot Laya questions, and a small local reasoning model. Validate classification thresholds, guardrails, and runbooks against labelled incidents before any production use.

Licensed under Apache-2.0.
