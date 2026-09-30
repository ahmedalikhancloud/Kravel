# Architecture

Kravel separates observation, reasoning, approval, and mutation into distinct security principals.

```text
                         read-only Kubernetes API
Browser ──▶ Debugger ─────────────────────────────────▶ cluster evidence
              │
              ├── input guardrail ─▶ LangGraph/Qwen ─▶ output guardrail
              │                           │
              │                           └── read-only tool loop
              │
              └── allowlisted fix ID ─▶ Approval broker
                                           │
                                           ├── fixed structured patch
                                           ├── Kubernetes dryRun=All
                                           ├── Local/real Slack approval ≤ 5m
                                           └── narrowly scoped live patch
```

## Read-only debugger

The `kravel-debugger` ServiceAccount can get, list, and watch common non-secret resources and read the `pods/log` subresource. It cannot create, update, patch, delete, exec, attach, proxy, or read Secrets. LangGraph receives six matching tool schemas and no generic shell or Kubernetes client.

The live browser map calls the same read-only API. Its CSS-3D objects are generated from the current Pod, Deployment, ConfigMap, and Service lists; they are not a historical reconstruction or simulated topology.

## Approval broker

The broker runs in a different Pod with a different ServiceAccount and SQLite database. Its namespaced Role grants `get` and `patch` only for these names:

- Deployments: `oom-demo`, `image-demo`, `crash-demo`, `config-demo`
- ConfigMap: `config-demo`

The broker accepts a `fixId`, never a command or user-supplied patch. Each fix ID resolves to immutable structured API operations in `kravel/fixes.py`. Creation first calls the Kubernetes API with `dryRun=All`. A human approval within 300 seconds moves the proposal to `approved`; only then does the worker repeat the exact catalog operation without dry-run.

The local approval credential exists only in the broker Pod and the localhost URL printed by the demo launcher. It is not mounted into the debugger Pod. Real Slack is an optional outbound adapter that posts the command and polls human emoji reactions.

## Audit and telemetry

The debugger and broker each append structured records to separate SQLite volumes. The browser merges their read-only audit feeds. Prometheus scrapes both services.

Each agent investigation creates an MLflow root span with nested spans for:

- input guardrail;
- each Qwen inference turn;
- each read-only Kubernetes tool call;
- output guardrail;
- deterministic current-issue discovery.

Prometheus also exposes end-to-end, Qwen, tool, guardrail, MLflow setup, span-overhead, and trace-flush timing. Approval status, age, approved fixes, and audited actions come from the broker.

## State and reset

Only audit and trace metadata is persisted. Kravel has no state-rewind or time-travel database. `scenario.sh` changes one disposable workload at a time; `reset.sh` reapplies the healthy baseline without restarting the observability stack.
