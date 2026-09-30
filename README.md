# Kravel

Kravel is a read-only Kubernetes incident agent with temporal memory. It continuously records Kubernetes object changes and Events, reconstructs the cluster at earlier timestamps, classifies incident-specific evidence with Laya and native Kubernetes signals, and conditionally escalates to a local Qwen agent built with LangGraph. Its local temporal cockpit visualizes the reconstructed topology, manifests, changes, warning events, and agent progress through Karl, a pixel-art Kubernetes time-travel copilot.

The supported demo runs entirely on one Windows laptop. Docker Desktop provides Kubernetes and local model inference. It needs no hosted model, API key, Codespace, public endpoint, or KillerCoda session.

## Incident path

```text
Kubernetes watch + Events → SQLite temporal reconstruction
                                  │
                                  ▼
               input guardrail → Laya System-1 → output guardrail
                                  │
                             policy gate
                     ┌────────────┴────────────┐
              routine/high confidence     severe/ambiguous
                     │                         │
             read-only runbook       Qwen input guardrail
                                               │
                                  LangGraph temporal tools
                                               │
                                       one Qwen synthesis
                                               │
                                  Qwen output guardrail
                     └────────────┬────────────┘
                                  ▼
                         human-review proposal
```

No remediation is executed. Both branches end at `awaiting_human_review` with `remediationExecuted: false`.

## Real tracing and metrics

Each pipeline run creates one MLflow trace with nested spans for reconstruction, both Laya guardrails, Laya inference, the policy gate, temporal tools, Qwen inference, the Qwen output guardrail, and proposal creation. Kravel separately measures MLflow setup, synchronous span overhead, and the explicit server flush that makes the trace durable. Trace inputs and outputs contain bounded metadata such as counts, decisions, and timings—not raw cluster evidence, prompts, reports, URLs, or credentials.

Prometheus and Grafana expose the same stage latencies, model timings, route, Laya probabilities, tool count, trace ID, and aggregated latency distributions.

## Local components

| Component | Location | Purpose |
|---|---|---|
| Docker Desktop Kubernetes | one local node | Runs workloads and services |
| Kravel (Python 3.12) | `kravel-system` | Watcher, temporal store, API, cockpit, LangGraph harness |
| Karl | Kravel cockpit | Grounded navigation, progress, options, and investigation approval |
| Laya | `kravel-ai`, CPU | Scores only ambiguous incident shards |
| Qwen3 4B Instruct | Docker Model Runner, GPU | Fast deep investigation and report synthesis |
| Prometheus + Grafana | `kravel-observability` | Aggregate stage latency |
| MLflow | `kravel-observability` | Inspect the nested trace waterfall |

## Quick start

Open **Git Bash** in the repository and run:

```bash
bash demo/local/prepare.sh
bash demo/local/demo.sh --scenario escalation
```

Then open:

- Kravel cockpit: `http://localhost:8080`
- Grafana: `http://localhost:3000`
- MLflow: `http://localhost:5000`, then select **Traces**

The cockpit opens on the captured incident window. Drag the timeline to reconstruct earlier cluster states, click any resource to inspect its historical YAML manifest and relationships, or ask Karl to explain the evidence. Deep local investigation requires an explicit UI approval; no remediation is executed.

The `.cmd` launchers call the same Bash files, so these are also valid from PowerShell or Command Prompt and do not depend on PowerShell execution policy:

```text
demo\local\prepare.cmd
demo\local\demo.cmd --scenario escalation
```

See [DEMO.md](DEMO.md) for the full setup, presentation flow, fast/Thinking model profiles, and troubleshooting.

## Read-only interfaces

LangGraph and the MCP server expose the same bounded temporal tools:

- `rewind_cluster_state(timestamp)`
- `diff_states(from, to)`
- `get_incident_context(incident_at, lookback)`
- `trace_resource(timestamp, resource_key)`

HTTP equivalents are available under `/v1/state/*` and `/v1/context`; `/metrics`, `/healthz`, and `/readyz` support operations.

The local UI additionally uses `/v1/timeline`, `/v1/incidents`, `/v1/karl/chat`, and the approval-gated `/v1/karl/analyze` endpoint. Obvious image-pull, scheduling, and Service/backend contradictions are derived from deterministic Kubernetes evidence. Laya receives only the remaining ambiguous shards instead of the whole namespace-wide event stream.

## Key configuration

| Variable | Default | Purpose |
|---|---|---|
| `KRAVEL_DB_PATH` | `data/kravel.db` | SQLite path |
| `KRAVEL_CLUSTER_ID` | hostname | Stable cluster identity |
| `KRAVEL_LAYA_URL` | internal Laya Service | System-1 endpoint |
| `KRAVEL_LLM_BASE_URL` | Docker Model Runner | Local OpenAI-compatible endpoint |
| `KRAVEL_LLM_MODEL` | `ai/qwen3:4b-instruct-2507-q4_K_M` | Fast local Qwen profile |
| `KRAVEL_LLM_REASONING_BUDGET` | `0` | Hidden-reasoning budget; zero for Instruct |
| `KRAVEL_LLM_TIMEOUT_SECONDS` | `90` | Local inference timeout |
| `KRAVEL_MLFLOW_URL` | internal MLflow Service | Trace destination |
| `KRAVEL_MLFLOW_EXPERIMENT` | `Kravel Local Incident Traces` | Trace experiment |

## Development

Kravel is Python-only:

```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -e '.[test]'
pytest
```

The image uses pinned dependencies from [pyproject.toml](pyproject.toml). The Laya image is pinned separately in [demo/local/laya.Dockerfile](demo/local/laya.Dockerfile).

## Safety status

This is an incident-analysis prototype, not an autonomous remediation system. Kubernetes fields and Events are untrusted input; guardrails reduce risk but are not a security boundary. Validate classifiers, thresholds, and runbooks on labelled incidents before production use. See [docs/security.md](docs/security.md).

Licensed under Apache-2.0.
