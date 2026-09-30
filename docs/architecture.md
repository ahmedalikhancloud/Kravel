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

The live browser map calls the same read-only API. Its WebGL objects and directional connections come from current Pod, Deployment, ReplicaSet, ConfigMap, and Service lists; they are not a historical reconstruction or simulated topology.

The cockpit starts an asynchronous investigation with one local model slot. A LangGraph evidence node collects bounded container state, rollout conditions, ownership, configuration, Events, Service selectors, EndpointSlices, and relevant current/previous logs. Failed reads are coverage gaps. Sanitized observations receive evidence IDs before local Qwen synthesis. Clear supported findings need one inference; uncertain findings can trigger one read-only follow-up round. SQLite run/step history records actual transitions and preserves partial evidence if inference fails. No hosted database or model router is used.

## Approval broker

The broker runs in a different Pod with a different ServiceAccount and SQLite database. Its namespaced Role grants `get` and `patch` only for these names:

- Deployments: `oom-demo`, `image-demo`, `crash-demo`, `config-demo`
- ConfigMap: `config-demo`
- Service: `demo-gateway`

The broker accepts a `fixId`, never a command or user-supplied patch. Each fix ID resolves to immutable structured API operations in `kravel/fixes.py`. Creation first calls the Kubernetes API with `dryRun=All`. A human approval within 300 seconds moves the proposal to `approved`; only then does the worker repeat the exact catalog operation without dry-run.

The local approval credential exists only in the broker Pod and the localhost URL printed by the demo launcher. It is not mounted into the debugger Pod. Real Slack is an optional outbound adapter that posts the command and polls human emoji reactions.

The ConfigMap restart annotation is unique to each reviewed proposal, so repeat demonstrations trigger a new rollout. Each operation is tracked separately; partial failure never triggers automatic retry. Patch acceptance is distinct from recovery. The independent read-only observer checks UID, reviewed fields, applied generation, current owned Pods, and readiness three times over at least six seconds, with a separate 90-second deadline. Service recovery requires Ready EndpointSlices targeting the HTTP lab. It does not imply a DNS/TCP probe or guarantee future health.

## Human-only console

The embedded page is served on localhost port 8082, a separate origin from Karl's page. A third ServiceAccount and separate generated unlock key are never mounted into debugger/broker Pods. An HttpOnly, SameSite=Strict, path-scoped 15-minute session and exact Origin/Host checks protect submissions. No command proxy exists in the debugger API.

This is a kubectl-compatible API console, not a Bash/PTY terminal: `shlex` parsing maps reviewed commands to bounded Kubernetes API calls, without launching a process. No host kubeconfig, Docker socket, scripts, plugins, pipes, exec, impersonation, context/credential overrides, or Secrets. Manual edits are restricted by namespace/name RBAC and reviewed fields/values (no arbitrary Pod templates). A write requires server dry-run and a one-use, session-bound 60-second confirmation; identity/spec/version checks reject stale previews. Iframe completion messages can only request a fresh read-only snapshot, never convey commands or credentials.

## Audit and telemetry

The debugger, broker, and operator append structured records to separate SQLite volumes. The browser merges their sanitized read-only audit feeds; Prometheus scrapes all three. SQLite is not a tamper-proof external audit sink.

Each agent investigation creates an MLflow root span with nested spans for:

- input guardrail;
- each Qwen inference turn;
- each read-only Kubernetes tool call;
- a separate guardrail scan of each tool result before it reaches Qwen;
- output guardrail;
- deterministic current-issue discovery.

Prometheus also exposes end-to-end, Qwen, tool, guardrail, MLflow setup, span-overhead, and trace-flush timing. Approval status, age, approved fixes, and audited actions come from the broker.
Repetitive API bookkeeping is removed only from model evidence, and the conversation is bounded for the local 12K-context model. Full read-only tool responses remain available in the UI; truncation is explicitly marked.

## State and reset

Audit records, bounded sanitized investigation observations/reports, workflow steps, and trace metadata are persisted locally. Kravel has no state-rewind or time-travel database. `scenario.sh` changes one disposable workload at a time; `reset.sh` reapplies five healthy labs without restarting observability. Investigations/verification interrupted by restart are marked interrupted, never replayed automatically.
