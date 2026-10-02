# Security model

Kravel is a debugging and repair agent with approval-gated, namespace-scoped writes.

## Guarantees in the local design

- The investigation worker ServiceAccount remains read-only. The agent's separate execution ServiceAccount has get/patch on existing Deployments, DaemonSets, ConfigMaps and Services in `kravel-demo`. No Secret, exec, attach, proxy, create, update, delete, RBAC or cluster-admin grants are added. This separation prevents direct investigation tools from bypassing approval; the overall agent is write-capable.
- The LLM has no shell, approval or direct Kubernetes mutation tool. It can draft structured repairs and stage `request_repair_approval`; review submission occurs only after input/evidence/output guards pass without output warnings. Submission runs server dry-run and requests human approval, not execution.
- Cluster and log text is treated as untrusted input. Instruction-like text is quarantined, credentials are redacted, and evidence is length-bounded before model use.
- Generated output is redacted and direct mutation commands are withheld. The exact displayed command comes from trusted code, not from Qwen.
- The executor Role is namespace-scoped, without `resourceNames`, so manually created supported resources are repairable. Default `KRAVEL_REPAIR_MODE=approval_gated` does not require enrollment. Optional `enrolled_only` restricts application policy to named fields; RBAC must also be narrowed separately for that deployment. Profile files remain read-only operator configuration; adding runbooks does not expand permissions.
- The broker accepts no arbitrary shell command. Novel patches pass deterministic kind/path/schema checks and target existing supported resources and containers only. Privilege/security-context changes, service-account changes, host networking, arbitrary metadata, merge directives and deletion are rejected. Server dry-run and separate human approval remain mandatory. See `docs/repair-coverage.md`.
- Every proposal must pass Kubernetes server-side dry-run and expire after five minutes without a human decision.
- The reviewed plan and resource identity/spec are checked again before execution. Resets or edits invalidate the proposal, and version preconditions reject concurrent changes. Each approval/execution is claimed atomically; interrupted executions are never replayed automatically.
- Read tools stay inside the operator-selected investigation namespace; the model cannot switch namespaces. Cluster-wide Node and Namespace inspection remains read-only.
- The approval token is not mounted into the debugger and is removed from the Local Slack address bar after page load.
- Port-forwards bind to `127.0.0.1`; no inbound internet endpoint is needed.
- Slack tokens, when used, come from environment variables and a Kubernetes Secret. They are never embedded in manifests or source.
- The human demo controller has its own origin (`127.0.0.1:8082`), Pod, ServiceAccount, unlock key, and database. Its scenario-only panel is embedded cross-origin; Karl has no lab/console tool or credential. Submissions require authenticated HttpOnly, SameSite=Strict sessions and exact Origin/Host checks. No cross-origin command access or debugger mutation proxy exists. The parent receives only bounded height and action-result notifications, never credentials or commands.
- Scenario buttons accept only fixed IDs/actions from `kravel/labs.py`, not resource names, namespaces, commands, patches, or URLs. They require server dry-run and one-use, session-bound confirmation within 60 seconds, revalidate all identities/specs before the first write, and use resourceVersion preconditions on each patch. Missing activity checks or active investigations/approvals block changes. Partial failure is reported, not rolled back or silently retried.
- Console commands map to bounded API calls, never a subprocess or host shell. Manual patch fields/values are allowlisted in addition to RBAC. Writes require session-bound, one-use dry-run confirmation within 60 seconds and reject stale identities/specifications.
- An API patch response is only acceptance. A read-only observer reports recovery, timeout, or interruption, without rollback or mutation retries.

## Important limitations

- A read-only agent can still see non-secret workload fields, ConfigMap data, logs, and Events. Do not put credentials in those locations.
- Kubernetes RBAC is the ultimate control boundary. Review `deploy/local.yaml` before adapting Kravel to a real cluster.
- NeMo Guardrails enforces custom input/output rails using local Qwen semantic checks in addition to deterministic preflight/redaction/tool restrictions. Required checks fail closed on unavailable framework/model, timeout, malformed/unknown classifier fields, incomplete output, or missing rail execution. See `docs/guardrails.md`. Semantic classifiers can be attacked or misclassify; Qwen is not a specialized moderation model. Model-based grounding review is not factual proof. Security does not depend on classifier obedience.
- Fresh presentation sessions hide completed history, not audit records or MLflow traces, and cannot hide active work. Clearing the view is not a cluster reset or data deletion.
- All write capabilities are for disposable `kravel-demo`. Additional namespaces, resource kinds or verbs require operator-reviewed RBAC and validator changes, not model instructions.
- Local Slack is a demo of the approval workflow, not a replacement for enterprise identity, retention, or separation-of-duties controls.
- Multi-object fixes are not transactions. If a later operation fails, the audit records any earlier applied operation; inspect the result and prepare a new review instead of automatically retrying.
- Anonymous Grafana access is convenient for localhost only. Do not expose this manifest directly outside the laptop.
- Local demo investigation traces use `KRAVEL_MLFLOW_CONTENT_MODE=redacted` to retain bounded sanitized questions, model messages/responses, tool evidence, and diagnoses in MLflow. The general configuration defaults to `metadata`; repair traces remain metadata-only. No raw capture mode is offered. Local investigation history also retains bounded sanitized observations, including log excerpts, and reports. Redaction is best-effort, not a guarantee that every secret format or sensitive URL is detected. Use non-sensitive demo workloads/questions; keep MLflow local and review traces/history before sharing. SQLite is not immutable and local administrators can alter it.
- RBAC restricts resource kinds, verbs and namespaces, not individual patch fields or human approval. The broker enforces field restrictions and the approval state machine. A compromised executor could patch any supported resource in `kravel-demo` without those application checks. Image/command changes can execute arbitrary container code with the workload's existing privileges. Production requires compatible admission policies, restricted workload identities, network isolation and stronger per-user approval controls; this local demo is not an enterprise security guarantee.
- Console sessions use local HTTP, not authenticated TLS, and share a demo-human actor rather than enterprise identity. Other laptop processes may reach localhost; protect the machine/key, lock the console, and rotate keys after public demonstrations.

## Real Slack scopes

Use a dedicated bot with only `chat:write` and `reactions:read`, invite it only to the approval channel, and rotate the token after a public demonstration. Never commit `.env` files, Kubernetes Secret output, private Local Slack/Demo controls links, or `.kravel-local-state.env`. Both generated unlock credentials are removed from address bars after page load; do not save them in chat or screen recordings. NeMo usage telemetry is explicitly disabled and embedding downloads are offline; classifiers call only the configured local model endpoint.

## Production hardening before reuse

Add authenticated TLS ingress, network policy, encrypted persistent storage, centralized append-only audit export, signed image provenance, admission policy, per-user approval identity, and a reviewed fix catalog. Replace localhost anonymous dashboards with authenticated deployments.
