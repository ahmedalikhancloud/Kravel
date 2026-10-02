# Security model

Kravel is a general Kubernetes operator with exact-plan, human-approved execution.
The local executor intentionally has **cluster-admin**, not namespace-scoped least
privilege. Use a disposable local cluster; an approved destructive plan or a
compromised executor can damage the whole cluster. See [operator boundaries](cluster-operator.md).

## Guarantees in the local design

- The investigation worker ServiceAccount remains read-only without Secret access. The separate approval broker is bound to cluster-admin and holds the approval credential. It exposes validated, redacted read calls plus exact-plan review submission, not an approval method to Karl. The whole agent can perform broad Kubernetes changes after independent approval.
- The LLM has no shell, approval or direct Kubernetes mutation tool. It can draft structured repairs and stage `request_repair_approval`; review submission occurs only after input/evidence/output guards pass without output warnings. Submission runs server dry-run and requests human approval, not execution.
- Cluster and log text is treated as untrusted input. Instruction-like text is quarantined, credentials are redacted, and evidence is length-bounded before model use.
- Generated prose is redacted and direct mutation commands are withheld there. General plans contain model-generated files and argv arrays validated by trusted code and displayed in full for independent human review. Credentials and literal Secret manifest payloads are rejected before review.
- General plans do not use the old four-kind/namespace/field enrollment restriction. They support arbitrary discovered resources, RBAC, CRDs, storage, node operations and bounded container exec, through cluster-admin. Legacy catalog/structured patch validators remain available; `enrolled_only` affects only that legacy path, not general plans or RBAC.
- The broker never launches a host shell or kubectl plugin. Fixed identity, no credential/context/server/impersonation override, no remote manifests or unreviewed host paths, reviewed generated files only. Arbitrary container code and powerful Kubernetes changes are possible after approval, including privilege/security-policy changes; examine their blast radius carefully.
- Kubernetes server dry-run runs where the command supports it. Unsupported/deferred validation is explicitly shown, not mislabeled passed, and requires acknowledgment at approval. Every pending request expires after five minutes without a human decision.
- General plans hash exact files/commands and recheck capturable target fingerprints before the first write. Uncapturable/selector/node-wide targets are explicitly command-level approvals, not transactions. Races after checks remain possible. Legacy patches retain UID/version preconditions. Each approval/execution is claimed atomically; interrupted executions never replay automatically.
- Traditional read wrappers use the selected namespace; validated `kubectl_read` supports other namespaces and dynamic API kinds. get output is JSON with Secret contents and last-applied annotations redacted. Templates/raw endpoints/host files are not accepted in the live read interface.
- The approval token is not mounted into the debugger and is removed from the Local Slack address bar after page load.
- Port-forwards bind to `127.0.0.1`; no inbound internet endpoint is needed.
- Slack tokens, when used, come from environment variables and a Kubernetes Secret. They are never embedded in manifests or source.
- The human demo controller has its own origin (`127.0.0.1:8082`), Pod, ServiceAccount, unlock key, and database. Its scenario-only panel is embedded cross-origin; Karl has no lab/console tool or credential. Submissions require authenticated HttpOnly, SameSite=Strict sessions and exact Origin/Host checks. No cross-origin command access or debugger mutation proxy exists. The parent receives only bounded height and action-result notifications, never credentials or commands.
- Scenario buttons accept only fixed IDs/actions from `kravel/labs.py`, not resource names, namespaces, commands, patches, or URLs. They require server dry-run and one-use, session-bound confirmation within 60 seconds, revalidate all identities/specs before the first write, and use resourceVersion preconditions on each patch. Missing activity checks or active investigations/approvals block changes. Partial failure is reported, not rolled back or silently retried.
- Console commands map to bounded API calls, never a subprocess or host shell. Manual patch fields/values are allowlisted in addition to RBAC. Writes require session-bound, one-use dry-run confirmation within 60 seconds and reject stale identities/specifications.
- An API patch response is only acceptance. A read-only observer verifies legacy catalog fixes. General plans use their explicit wait/rollout/read checks, never a hard-coded demo recovery heuristic. Successful commands are not a blanket application-health guarantee.

## Important limitations

- A read-only agent can still see non-secret workload fields, ConfigMap data, logs, and Events. Do not put credentials in those locations.
- Kubernetes RBAC is the ultimate control boundary. Review `deploy/local.yaml` before adapting Kravel to a real cluster.
- NeMo Guardrails enforces custom input/output rails using local Qwen semantic checks in addition to deterministic preflight/redaction/tool restrictions. Required checks fail closed on unavailable framework/model, timeout, malformed/unknown classifier fields, incomplete output, or missing rail execution. See `docs/guardrails.md`. Semantic classifiers can be attacked or misclassify; Qwen is not a specialized moderation model. Model-based grounding review is not factual proof. Security does not depend on classifier obedience.
- Fresh presentation sessions hide completed history, not audit records or MLflow traces, and cannot hide active work. Clearing the view is not a cluster reset or data deletion.
- The local broad executor can affect ANY namespace and cluster-scoped resource. Do not install it unchanged in production. Disabling `KRAVEL_CLUSTER_OPERATOR_MODE` stops general-plan execution but does not revoke RBAC; delete `kravel-approved-cluster-operator` ClusterRoleBinding to remove the broad grant.
- Local Slack is a demo of the approval workflow, not a replacement for enterprise identity, retention, or separation-of-duties controls.
- Multi-object fixes are not transactions. If a later operation fails, the audit records any earlier applied operation; inspect the result and prepare a new review instead of automatically retrying.
- Anonymous Grafana access is convenient for localhost only. Do not expose this manifest directly outside the laptop.
- Local investigation AND broker traces use `KRAVEL_MLFLOW_CONTENT_MODE=redacted`; general configuration defaults to metadata-only. Questions, tool authorization, selected models, plans, validation and execution outputs are bounded/redacted in tracing. Exact credential-screened source files are retained separately for faithful review. No raw trace mode is offered. Redaction is best-effort: do not put private values in workloads, chat or generated files; keep MLflow local and review history before sharing. SQLite is not immutable; local administrators can alter it.
- Cluster-admin RBAC cannot enforce human approval. The broker's application state machine is the mutation gate; a compromised broker can bypass it and control the whole cluster. Admission policy, restricted executor roles, network isolation, stronger per-user approver identity and tamper-resistant audit export are required for a production design. This local demo is not an enterprise security guarantee.
- Console sessions use local HTTP, not authenticated TLS, and share a demo-human actor rather than enterprise identity. Other laptop processes may reach localhost; protect the machine/key, lock the console, and rotate keys after public demonstrations.

## Real Slack scopes

Use a dedicated bot with only `chat:write` and `reactions:read`, invite it only to the approval channel, and rotate the token after a public demonstration. Never commit `.env` files, Kubernetes Secret output, private Local Slack/Demo controls links, or `.kravel-local-state.env`. Both generated unlock credentials are removed from address bars after page load; do not save them in chat or screen recordings. NeMo usage telemetry is explicitly disabled and embedding downloads are offline; classifiers call only the configured local model endpoint.

## Production hardening before reuse

Add authenticated TLS ingress, network policy, encrypted persistent storage, centralized append-only audit export, signed image provenance, admission policy, per-user approval identity, and a reviewed fix catalog. Replace localhost anonymous dashboards with authenticated deployments.
