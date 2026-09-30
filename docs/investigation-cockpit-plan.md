# Kravel investigation cockpit — integration design

Status: implemented with the user-selected demo-scoped kubectl-compatible console. See DEMO.md and docs/security.md for the current workflow and limitations.

## Outcome

Keep one Kravel application: the existing 3D topology, Karl, local Qwen/LangGraph, SQLite, approval broker, Prometheus, Grafana, and MLflow. Add an evidence-first investigation pipeline, an observable repair workflow, and a separate human-operated terminal pane. Do not introduce InsForge, OpenRouter, hosted models, a second database, or a replacement frontend framework.

The reference prompts specify broader evidence collection, evidence correlation, structured diagnoses, and investigation progress. They do not implement approved repair execution or post-repair verification. Those should extend Kravel's existing safeguards rather than replace them.

Reference material, used as design inspiration rather than copied source:

- [Evidence collection](https://github.com/iam-veeramalla/AI-DevOps-Kubernetes-Agent/blob/main/prompts/02-k8s-investigation-engine-prompt.md)
- [Structured reasoning](https://github.com/iam-veeramalla/AI-DevOps-Kubernetes-Agent/blob/main/prompts/03-AI-reasoning-engine-prompt.md)
- [Progress and investigation history](https://github.com/iam-veeramalla/AI-DevOps-Kubernetes-Agent/blob/main/prompts/04-insfoge-backend-prompt.md)
- [End-to-end demonstrations](https://github.com/iam-veeramalla/AI-DevOps-Kubernetes-Agent/blob/main/prompts/05-end-to-end-integration-prompt.md)

## Changes from the current implementation

| Area | Current Kravel | Proposed extension |
|---|---|---|
| Investigation | Qwen chooses among six bounded read tools | Deterministic initial evidence pass, then focused Qwen follow-up when needed |
| Coverage | Four demo failure types plus general read tools | Pod/container state, ownership, effective configuration, Events, rollout conditions, Service selectors, and EndpointSlices |
| Live progress | Elapsed investigation time | Actual step start/completion/failure records, with resource and evidence IDs |
| Diagnosis | Guarded prose and catalog suggestions | Cause, supporting observations, competing explanations, missing evidence, prevention, and a reviewable next action |
| Fix status | Broker marks structured patches executed | Distinct patch acceptance, rollout convergence, and observed recovery states |
| History | Timing metadata and audit entries | Local run summaries and bounded sanitized evidence references; no time-travel reconstruction |
| Terminal | External Git Bash window | Human-only pane, isolated from the agent and approval broker |

## Investigation flow

1. Scope the run to the selected cluster/namespace and, optionally, the selected resource. Default to the existing Docker Desktop demo. Do not automatically investigate every kubeconfig context, including possible production contexts.
2. Inspect Pods and per-container states, exit reasons, restart counts, readiness, image names, requests, and limits. Distinguish Running from Ready. Inspect scheduling and mount failures as well as the existing crash/image/memory failures.
3. Collect relevant current logs; request previous logs only for containers with a prior execution. An unavailable log is a missing evidence item, not a failed whole investigation. Bound line counts and record which container produced each item.
4. Read relevant Events, including their time, reason, object UID, and repetition count. Separate older warnings from the currently observed failure.
5. Follow real Pod → ReplicaSet → Deployment ownership. Inspect desired/updated/available replicas, observed generation, conditions, and configuration references. Do not infer ownership from similar names.
6. Check Services against their selectors and EndpointSlices. Distinguish no matching Pods, matching but unready Pods, and ready endpoints. Record DNS/HTTP connectivity as untested unless there is an actual probe result; resource references are not proof of traffic.
7. Feed a compact, redacted evidence bundle to local Qwen. Keep LangGraph and all existing input, tool-evidence, and output guardrails. Permit bounded read-only follow-up calls for unresolved hypotheses.
8. Present the diagnosis with evidence IDs and resource links. Clicking either opens the existing Inspector and highlights the relevant 3D objects and connections.

Use evidence-strength labels with explanations (strong, moderate, insufficient), not an uncalibrated LLM-generated percentage presented as a probability. Confidence never bypasses approval. Do not invent an autonomous fix for cases outside the reviewed catalog.

For demo responsiveness, batch deterministic reads and use one synthesis call for clear cases. Add more model turns only when they answer a specific unresolved question. Limit concurrent local model runs and keep partial results visible when a read or model call fails.

## Step-by-step repair tracking

```text
Investigate → Evidence → Diagnosis → Review plan → Server dry-run
  → Human approval (≤5 minutes) → Revalidate → Apply each operation
  → Observe rollout → Verify recovery / report timeout or failure
```

Every stage reflects backend state; no simulated progress ticks. Each operation exposes its exact target, dry-run result, application result, duration, and relevant observed resources. The browser shows the pending approval deadline and can follow a run after refresh.

Retain the current broker principal, immutable fix catalog, stale-plan checks, version preconditions, and atomic execution claim. A patch response means Kubernetes accepted a change, not that the incident is fixed. A separate read-only verifier observes the applied generation, new owned Pods, and readiness; it never retries mutations or automatically rolls back.

For ConfigMap repair, show the ConfigMap update and Deployment restart as distinct operations. Verify the replacement workload, not an older Ready Pod. If the second operation fails, show the first operation as applied and the overall workflow as partially failed. Require a new reviewed proposal for further mutations.

A verification deadline is separate from the five-minute approval deadline. On timeout, show the latest evidence and leave the cluster untouched. Recovery should require repeated successful observations over a small stability window; describe this as observed recovery, not a guarantee of future health.

Store bounded run/step records locally in SQLite, linked to audit entries, proposals, and MLflow traces. Add evidence collection, each fix operation, approval wait, and verification spans/timers to existing observability. Persist interrupted status after restart; never silently replay a repair.

## Terminal decision and security boundary

The user chose the first of these capabilities. The shipped console maps parsed commands directly to Kubernetes API calls; no kubectl process or shell is launched.

| Option | Capability | Trade-off |
|---|---|---|
| Demo-scoped kubectl console — recommended | Real cluster reads and reviewed manual edits to the disposable labs, with command history and output | Not a full Bash/PTY shell; no arbitrary scripts, pipes, interactive editors, or host filesystem access |
| Full local Bash terminal | Interactive Bash using explicitly selected local Kubernetes permissions | Can change anything those permissions allow and access host files; substantially larger security and audit surface |

In both options, Karl must receive no terminal tool, terminal session, terminal credential, or execution endpoint. Model text must never auto-execute or be injected into the terminal. An operator explicitly types and submits manual commands. Manual writes bypass the AI approval workflow and must be labeled and audited as operator actions.

The recommended console should use a separate operator service/principal, a separate unlock credential not mounted into the debugger, and localhost-only exposure. Embed its UI on a separate origin so the debugger page cannot read the privileged credential. Require authentication and an exact Origin check for command submissions; do not add a generic command route to the unauthenticated debugger API.

The shipped console parses a bounded kubectl-compatible grammar and sends structured Kubernetes API requests; it launches neither kubectl nor a shell. Pin the cluster and namespace server-side. Reject plugins, credential/context overrides, impersonation flags, Secret reads, exec/proxy, arbitrary file/URL inputs, and unbounded long-running commands. Enforce output/time limits and a narrowly reviewed manual-edit grammar. Merely granting namespace-wide workload writes is insufficient protection: arbitrary Pod template edits can create privileged Pods or host mounts, so restrict editable fields or enforce a compatible admission policy.

Command display, execution history, and audit must redact credentials. Do not claim a full Bash session offers complete semantic command auditing; interactive programs and shell expansion complicate that guarantee.

After a command completes, trigger a fresh read-only snapshot and then continue normal polling. Show replacement Pods and changed ownership only when Kubernetes reports them. Pinning the selected context prevents a demo from accidentally targeting another cluster.

## Strongest live demonstration

Retain the four independent OOM, image, crash, and ConfigMap scenarios and add a real networking scenario: a Service selector mismatch with a small working HTTP workload behind it.

The original gateway pointed to a sleeping ConfigMap lab, so ready endpoints did not demonstrate an HTTP server. The implemented `net-demo` workload serves HTTP and has an HTTP readiness probe; `demo-gateway` now selects it. Keep its approved Service repair restricted to that named disposable Service; do not broaden broker permissions to arbitrary networking resources. Endpoint verification still does not claim a client-side DNS or traffic test.

Demo sequence:

1. Open the 3D view and the embedded operator pane.
2. Break one lab manually; show its real resource change propagating into the map.
3. Select the affected object and start an investigation.
4. Follow real evidence steps and click their linked manifests, Events, or logs.
5. Show the cause, uncertainty, proposed repair, and prevention advice.
6. Review dry-run, approve in Local Slack, and watch each approved operation.
7. Follow the replacement Pod and verify observed readiness before showing recovery.
8. Repeat with a Service selector mismatch: disappearing selector links, absent matching endpoints, approved selector repair, and restored endpoint readiness.
9. Show approval expiry, stale-plan rejection, partial evidence, or verification timeout as honest failure paths.
10. Reset the disposable labs; retain the audit, traces, and dashboards.

## Implementation order and acceptance checks

1. Evidence collector, local run/step persistence, bounded job API, and progress UI.
2. Structured Qwen synthesis and evidence-to-topology links.
3. Per-operation broker progress and separate read-only recovery verification.
4. Working HTTP networking lab, selector diagnosis, and narrowly approved Service repair.
5. The chosen isolated manual terminal/console and launcher integration.
6. Bash-only demo instructions, end-to-end browser checks, credential review, commit, and push.

Verify that progress corresponds to completed backend work; missing permissions yield partial evidence; results identify the actual resource/container; manual changes appear only after fresh cluster reads; the agent cannot invoke the operator service; Secrets and unsafe flag combinations are rejected; unapproved, expired, stale, or interrupted repairs never run; and a successful patch with an unhealthy rollout is not marked recovered. Run all existing RBAC, broker, guardrail, topology, API, and UI tests alongside the new coverage.
