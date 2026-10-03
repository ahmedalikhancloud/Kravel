# Architecture

Kravel separates observation, reasoning, approval, and mutation into distinct security principals.

```text
                         read-only Kubernetes API
Browser ──▶ Debugger ─────────────────────────────────▶ cluster evidence
              │
              ├── input + scope guardrails ─▶ LangGraph/Qwen ─▶ output guardrail
              │                           │
              │                           └── read tools + structured repair drafts
              │
              └── guarded fix ID / novel draft ─▶ Repair executor / approval broker
                                           │
                                           ├── deterministic structured patch validation
                                           ├── Kubernetes dryRun=All
                                           ├── Local/real Slack approval ≤ 5m
                                           └── narrowly scoped live patch
```

## Repair agent with separate investigation identity

Karl is write-capable through its approval-gated execution service. Its investigation identity, `kravel-debugger`, can get/list/watch non-secret resources and read `pods/log`, but cannot mutate Kubernetes, exec, attach, proxy, or read Secrets. LangGraph gets read tools, bounded reference retrieval, structured draft creation and a review-request tool; never a generic shell, approval credential or directly writable Kubernetes client. This prevents investigation tools from bypassing the manual gate.

The live browser map calls the same read-only API. Its WebGL objects and directional connections come from current Pod, Deployment, ReplicaSet, ConfigMap, and Service lists; they are not a historical reconstruction or simulated topology.

The guided page starts an asynchronous request with one local model slot. Before cluster reads, redaction/quarantine and deterministic preflight (`karl-preflight-v2`) screen requests. Help/redirect/reject return local explanations without model calls or evidence collection. Candidate requests must then pass NVIDIA NeMo Guardrails custom input rails (`karl-layered-v5`): a short, tools-free local Qwen classifier evaluates professional language, instruction integrity, actual domain/task meaning, and learning/investigation mode. Classifiers request schema-constrained JSON and independently validate it; schema/errors/timeouts fail closed. Keywords are not final authorization. Each accepted evidence bundle/tool result passes a separate injection rail before it is used. Final responses pass output safety and evidence-support rails; failed output is withheld and fixes suppressed. See `docs/guardrails.md` for policies and limitations.

For allowed requests, a LangGraph evidence node collects bounded container state, rollout conditions, ownership, configuration, Events, Service selectors, EndpointSlices, and relevant current/previous logs. Failed reads are coverage gaps. Sanitized observations receive evidence IDs before local Qwen synthesis. Clear supported findings need one inference; uncertain findings can trigger one read-only follow-up round. SQLite run/step history records actual transitions and preserves partial evidence if inference fails. No hosted database or model router is used. The UI does not label model prose a guaranteed grounded diagnosis.

Generic object-learning questions without a selected resource take a separate LangGraph entry to Qwen with a teacher prompt, no tool schemas, no evidence collection, and no issue discovery. The result is labeled a general explanation, not a live assessment. Resource-specific/current-state wording stays on the investigation path. Both paths retain input, scope, inference, and output checks with actual stage timing.

## Approval broker

The execution service runs in a different Pod with the `kravel-approval-broker`
ServiceAccount and a separate SQLite database. The local general-operator
installation now binds this identity to **cluster-admin**; it is no longer
namespace-scoped least privilege. The worker stays read-only, without approval
credentials. General plans support arbitrary discovered API kinds, generated
files and ordered built-in kubectl commands, including cross-namespace changes,
RBAC, CRDs, storage and bounded exec/node operations. See
[the operator design and limitations](cluster-operator.md).

The broker accepts a catalog `fixId`, a strictly validated structured draft, or
a canonical general file/argv plan. It never launches a host shell or plugin.
`request_repair_approval` stages a review selection
only if the operator requested repair and it belongs to live findings/drafts in
this investigation, or a general plan staged by that investigation. Submission is deferred until output guards pass cleanly.
The UI can also forward a draft from a saved, permitted, completed investigation.

Legacy catalog/draft proposals resolve to exact structured API operations, validate existing
container identity and calls Kubernetes with `dryRun=All` before requesting human
approval. The command is generated from the patch, not model prose. A human
approval within 300 seconds moves the proposal to `approved`; only then can the
worker apply the reviewed operations. It revalidates current policy, plan hash,
UID and specification, with resourceVersion preconditions. Denial, timeout,
staleness, policy revocation and interrupted execution never trigger automatic
replay. No agent tool can approve a proposal.

General plans hash all exact files, argv arrays and step labels; the broker uses
server dry-run where kubectl supports it. Missing/deferred validation requires
human acknowledgment. Capturable named/declarative targets are fingerprinted
before review and rechecked before the first write; other operations are
command-level approvals, not transactions. Approved steps run once in order,
stop on failure, and record real progress/check outputs. General plans do not
use the demo-specific recovery observer. Local Fast/Auto/Thinking routing is a
deterministic LangGraph stage recorded in MLflow; classifiers remain on fast Qwen.

The local approval credential exists only in the broker Pod and the localhost URL printed by the demo launcher. It is not mounted into the debugger Pod. Real Slack is an optional outbound adapter that posts the command and polls human emoji reactions.

The ConfigMap restart annotation is unique to each reviewed proposal, so repeat demonstrations trigger a new rollout. Each operation is tracked separately; partial failure never triggers automatic retry. Patch acceptance is distinct from recovery. The independent read-only observer checks UID, reviewed fields, applied generation, current owned Pods, and readiness three times over at least six seconds, with a separate 90-second deadline. Service recovery requires Ready EndpointSlices targeting the HTTP lab. It does not imply a DNS/TCP probe or guarantee future health.

## Human-only demo controls and legacy operator compatibility

The operator's root page is now a small scenario-button panel embedded from localhost:8082, which the launcher forwards to loopback only. Its third ServiceAccount and separate generated unlock key are never mounted into debugger/broker Pods. Open the printed private fragment link once to grant an HttpOnly, SameSite=Strict, path-scoped 15-minute session, then reload Kravel. The frame can submit only from its exact Origin/Host; the parent has no credential/CORS access and exchanges only height/action-result notifications. No lab mutation proxy or action tool exists in the debugger API.

`LabController` exposes fixed scenario IDs, break-one/reset-all actions, and session-bound dry-run previews. No client resource/command/patch fields are accepted. All resource identities/spec fingerprints are rechecked before any apply, then each patch gets current version preconditions. Changes outside five named Deployments, one ConfigMap and one Service are denied by RBAC. Active investigations/approvals and unavailable activity checks stop the action. Accepted patches are not advertised as observed failure/recovery. Reset leaves stored records intact; users clear the view only after observing health. Multi-object actions can partially succeed and never retry/roll back automatically.

The former console is available only at `/console` for compatibility, not in the guided UI. Manual Bash commands belong in Git Bash.

It is a kubectl-compatible API console, not a Bash/PTY terminal: `shlex` parsing maps reviewed commands to bounded Kubernetes API calls, without launching a process. No host kubeconfig, Docker socket, scripts, plugins, pipes, exec, impersonation, context/credential overrides, or Secrets. Manual edits are restricted by namespace/name RBAC and reviewed fields/values (no arbitrary Pod templates). A write requires server dry-run and a one-use, session-bound 60-second confirmation; identity/spec/version checks reject stale previews.

## Audit and telemetry

The debugger, broker, and operator append structured records to separate SQLite volumes. The guided browser does not load or display audit feeds; logging and Prometheus scraping remain enabled. SQLite is not a tamper-proof external audit sink.

Each agent investigation creates an MLflow root span with nested spans for:

- input guardrail;
- fast request preflight before any model call, then NeMo semantic input policy before diagnostic inference or reads;
- policy-classifier calls in separately named nested spans, including strict flags/reasons, framework/version, and elapsed time;
- each Qwen inference turn;
- each read-only Kubernetes tool call;
- a separate guardrail scan of each tool result before it reaches Qwen;
- output guardrail;
- staged repair-review authorization and post-guard broker submission, when requested;
- deterministic current-issue discovery.

Prometheus also exposes end-to-end, Qwen, tool, guardrail, MLflow setup, span-overhead, and trace-flush timing. Approval status, age, approved fixes, and audited actions come from the broker.
Repetitive API bookkeeping is removed only from model evidence, and the conversation is bounded for the local 12K-context model. Full read-only tool responses remain available in the UI; truncation is explicitly marked.

## State and reset

Audit records, bounded sanitized investigation observations/reports, workflow steps, and traces are persisted locally. The demo enables bounded redacted investigation content in MLflow; general configuration and repair traces default to metadata-only. Kravel has no state-rewind or time-travel database. `scenario.sh` changes one disposable workload at a time; `reset.sh` reapplies five healthy labs without restarting observability. Investigations/verification interrupted by restart are marked interrupted, never replayed automatically.

Each SQLite store persists a presentation-session boundary. Clean launch/reset advances the debugger and Local Slack boundaries after restoring healthy labs; completed older work stays in storage but not the current view. Active work is never filtered away, and boundary advancement fails closed while investigation/verification/approval is active. The page's clear-view action advances only the debugger boundary and does not reset Kubernetes. Real cluster health is always displayed, independent of the history filter. Repair checklist checkmarks require recorded completed steps, including separate readiness and stability stages; an accepted patch is not treated as verified recovery.
