# Karl: general, human-approved Kubernetes operations

Karl is no longer confined to the repair catalog or four resource kinds. The local
installation supports creation, modification and deletion of arbitrary Kubernetes
API resources: other namespaces, CRDs/custom resources, Jobs, RBAC, storage,
networking, node operations and bounded container commands.

The model creates an **exact plan**, not a shell string. Its files and commands
are shown in Kravel and the separate approval inbox. No mutation occurs during
chat or planning. Every execution requires an independent human decision within
five minutes. Approval permits only the reviewed ordered steps, not subsequent
model-generated changes.

## Diagnosis, researched recommendations, and validation feedback

**Investigate problems** is diagnosis-only: Karl can read evidence and recommend
next checks, but it cannot stage a mutation or submit an approval request for that
button. An explicit request such as `Fix the daemonset named example-daemonset`
enables approval-gated repair planning instead.

For missing or obsolete image tags, Karl can actively look up published versions,
digests and Linux architectures in vetted public Docker Hub repositories and read
approved vendor documentation/official-image tag lists. This costs no API key or
paid service, but requires internet access and can be rate-limited. It sends only
public repository/tag/version identifiers, not cluster logs, private registry
URLs, configuration, credentials or user prompts. Private/arbitrary repositories
are not queried. Availability is not proof of compatibility or security: Karl
must preserve the component's purpose, explain migration risks, and include
rollout/readiness checks in the exact reviewed plan. It should ask you for missing
application requirements only after research cannot establish a suitable choice.

For image-only repairs, `draft_image_update` generates a minimal named `set image`
operation and bounded rollout/readiness check from researched evidence. It reads
the actual container and does not regenerate YAML, placement or tolerations.
Broader changes still use the general command-and-file planner.

Minimal named patches are preferred for one-field repairs. A server dry-run checks
API schema/admission; it does **not** pull the image or test application startup.
If validation rejects a plan, its card keeps the failing step and Kubernetes
diagnostics visible. No review is created and no change is applied. The failed
validation and rejection are also recorded in MLflow. Ask Karl to revise the
invalid plan; do not approve an older guessed image recommendation.

A missing previous log is an evidence gap, not an app exception or proof of a
crash. If delivery times out, check the separate approval inbox before retrying:
a lost response may conceal an already-created review request. Karl never retries
approval submission or execution automatically.

## Upgrade without resetting the cluster

Run from the repository folder in Git Bash:

```bash
git pull --ff-only
docker model pull ai/qwen3:4b-thinking-2507-q4_K_M
bash demo/local/upgrade-debugger.sh
bash demo/local/demo.sh --connect-only
```

This intentionally grants **cluster-admin to the approval broker**. It preserves
your workloads, observability data and private approval/demo credentials. Karl's
investigation service account remains read-only and cannot read approval Secrets.
The broker offers a separately validated read interface with Secret contents
redacted, including last-applied annotations. It never returns its credentials.

This broad executor is not least-privilege production hardening: compromise of
the broker, or approval of a dangerous plan, can compromise the whole cluster.
Use a disposable local cluster. Review RBAC, privileged workloads, node changes,
namespace deletion, PVC/PV deletion and application data changes particularly
carefully. Guardrails are screening, not a guarantee against mistakes.

## A straightforward demo

1. Open Kravel and **Ask Karl**. Keep `kravel-demo` selected initially.
2. Choose **Auto** or **Thinking** in **Local model route**.
3. Ask:

   > Inspect for name collisions, then create a ConfigMap hello-settings with
   > GREETING=hello, a Deployment hello-demo with one nginx:1.27.5 container on
   > port 80 reading the ConfigMap through envFrom, and a ClusterIP Service
   > hello-demo on port 80 with matching selectors in kravel-demo. Generate the
   > YAML, include rollout and endpoint checks, and request human approval.

4. Expand **Inspect commands & generated code** to read/download the saved files.
5. Open the private **Local Slack** launcher link, or review the complete thread
   in configured real Slack. Inspect commands, files and validation results.
6. Approve **only if the exact plan meets your request**. Watch each recorded
   step turn into a checkmark. The live map refreshes; choose another namespace
   in its selector when your request targets one.
7. Ask Karl to remove those **three specifically named demo objects** if desired;
   deletion needs its own new review and approval. Never reset the whole namespace
   just to remove a newly created application.

The small local model can still produce an invalid plan or ask for missing
information. Failed server validation prevents submission unless the specific
step declares earlier prerequisites; deferred validation is clearly flagged and
must pass immediately before that step actually executes. If output guards return
warnings, automatic submission is withheld; you can inspect a permitted saved
plan and manually request review. A blocked investigation cannot promote a plan.

## Local model orchestration

- **Fast:** local Qwen instruct for investigation/planning and all policy checks.
- **Thinking:** fast Qwen first discovers relevant live state, the thinking
  model gets one planning turn, then fast Qwen repairs schema errors or summarizes
  the proposal. Input, evidence and output screening also stay on fast Qwen.
  The thinker does not run an expensive retry loop. Thinking can still be slower.
- **Auto:** deterministic routing chooses thinking for complex/multi-resource
  requests and fast for simple reads/learning. No cloud router, external database,
  extra credentials or parallel resident agent processes are introduced.

`agent.model_router` records the selected model, role, reason and policy model in
MLflow. Model calls, tool authorization, canonical plan creation, approval request,
broker validation and execution steps have nested spans. Private chain-of-thought
is not displayed; concise conclusions, generated code and tool results are.
Each inference span and UI progress step records its actual model and agent role.
Plan validation errors are visible in the step details. A successfully staged
general plan automatically queues review for an explicit change request, but only
after final output guards pass; this never approves or executes anything.

Preparation/upgrade configures a 1,024-token thinking budget and the selected
context size using [Docker Model Runner runtime flags](https://docs.docker.com/ai/model-runner/configuration/).
Override it in Git Bash with `export KRAVEL_THINKING_BUDGET=2048` before upgrading
if you prefer deeper/slower planning. A budget is not a wall-clock latency promise;
model loading, hardware, context processing, tool reads and screening still matter.

Optional environment settings: `KRAVEL_LLM_ROUTING=fast|auto|thinking`,
`KRAVEL_LLM_THINKING_MODEL` and `KRAVEL_CLUSTER_OPERATOR_MODE=cluster|disabled`.
The local manifest defaults to `cluster` and `auto`. Setting operator mode to
disabled stops new general plans and execution of previously pending general
plans, but **does not revoke RBAC**. To remove the broad permission itself:

```bash
kubectl delete clusterrolebinding kravel-approved-cluster-operator
```

## Validation and boundaries

Generated files are flat relative names; commands are `kubectl` argv arrays,
executed with a fixed in-cluster identity and **no host shell**. No kubeconfig,
context, server, impersonation, TLS or credential override is accepted. Plugins,
remote manifests, arbitrary host paths, interactive editors, TTY sessions and
unbounded streams are not approval plans. For an interactive terminal, use your
own Git Bash; for a cluster change use declarative files or bounded commands.
Reviewed container scripts can run through explicit `exec ... -- ...` after
approval, or be mounted as application code through a reviewed ConfigMap.
`cp` only uploads a generated reviewed file to a container, not host downloads.

There is **no universal Kubernetes dry-run**. The broker checks kubectl support
and uses `--dry-run=server` where available. `exec`, `cp` and certain subcommands
are not executed during preview; the missing validation is prominently marked.
Local approval requires a risk acknowledgment checkbox; a real Slack thumbs-up
explicitly accepts the limitations printed in the complete review thread.

All command/file bytes are bound to a SHA-256 plan hash. Named/declarative targets
are fingerprinted when they can be captured and rechecked before the first write.
Selector-wide, node-wide, exec and other uncapturable targets are explicitly
command-level approvals, not frozen-object or transactional guarantees. A race
after revalidation is still possible: general kubectl operations are not an
atomic transaction. Existing catalog patches retain their UID/version checks.

Execution is ordered and at-most-once. A failed step stops later steps; previous
successful changes are retained for inspection. Timeouts and broker restarts
never cause automatic retry, rollback or replay. Remote container processes may
outlive a timed-out exec client, so inspect before preparing a replacement plan.
Include explicit `wait`, `rollout status` or read checks in the plan: exit code 0
alone is not an application-health guarantee. General plans never use the
demo-specific recovery heuristic.

Do not paste credentials into chat. Literal Secret payloads in generated
manifests are rejected before review; provision sensitive values separately and
have Karl reference existing Secrets. Slack code review uses complete threaded
chunks; reactions are not polled until publication succeeds. Real Slack remains
optional: without its existing bot/channel configuration only Local Slack runs.
