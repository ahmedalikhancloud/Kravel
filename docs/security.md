# Security model

Kravel is intentionally a debugger first and a tightly bounded demo repair workflow second.

## Guarantees in the local design

- The agent ServiceAccount is read-only and has no Secret, exec, attach, proxy, create, update, patch, or delete permissions.
- The LLM has no shell tool and no mutation tool. It can return only prose and known fix IDs.
- Cluster and log text is treated as untrusted input. Instruction-like text is quarantined, credentials are redacted, and evidence is length-bounded before model use.
- Generated output is redacted and direct mutation commands are withheld. The exact displayed command comes from trusted code, not from Qwen.
- The broker uses a separate ServiceAccount restricted with `resourceNames` to six disposable objects in `kravel-demo`: four Deployments, one ConfigMap, one Service.
- The broker accepts no arbitrary command or arbitrary patch.
- Every proposal must pass Kubernetes server-side dry-run and expire after five minutes without a human decision.
- The reviewed plan and resource identity/spec are checked again before execution. Resets or edits invalidate the proposal, and version preconditions reject concurrent changes. Each approval/execution is claimed atomically; interrupted executions are never replayed automatically.
- Read tools stay inside the operator-selected investigation namespace; the model cannot switch namespaces. Cluster-wide Node and Namespace inspection remains read-only.
- The approval token is not mounted into the debugger and is removed from the Local Slack address bar after page load.
- Port-forwards bind to `127.0.0.1`; no inbound internet endpoint is needed.
- Slack tokens, when used, come from environment variables and a Kubernetes Secret. They are never embedded in manifests or source.
- The legacy operator backend has its own origin, Pod, ServiceAccount, unlock key, and database. It is absent from the guided UI and has no default launcher port-forward. Karl has no console tool or credential. If separately exposed, submissions require authenticated sessions and exact Origin/Host checks, with no cross-origin command access.
- Console commands map to bounded API calls, never a subprocess or host shell. Manual patch fields/values are allowlisted in addition to RBAC. Writes require session-bound, one-use dry-run confirmation within 60 seconds and reject stale identities/specifications.
- An API patch response is only acceptance. A read-only observer reports recovery, timeout, or interruption, without rollback or mutation retries.

## Important limitations

- A read-only agent can still see non-secret workload fields, ConfigMap data, logs, and Events. Do not put credentials in those locations.
- Kubernetes RBAC is the ultimate control boundary. Review `deploy/local.yaml` before adapting Kravel to a real cluster.
- The request-scope gate is a transparent deterministic heuristic, not a complete semantic classifier or prompt-injection defense. It can misclassify unusual wording. Output redaction, mutation-command withholding, and uncertainty-marker checks do not establish factual grounding. Security does not depend on the model following these checks.
- Fresh presentation sessions hide completed history, not audit records or MLflow traces, and cannot hide active work. Clearing the view is not a cluster reset or data deletion.
- The bundled repair catalog is for the disposable `kravel-demo` namespace. Do not widen its namespace, resource names, or verbs without a separate security review.
- Local Slack is a demo of the approval workflow, not a replacement for enterprise identity, retention, or separation-of-duties controls.
- Multi-object fixes are not transactions. If a later operation fails, the audit records any earlier applied operation; inspect the result and prepare a new review instead of automatically retrying.
- Anonymous Grafana access is convenient for localhost only. Do not expose this manifest directly outside the laptop.
- Local demo investigation traces use `KRAVEL_MLFLOW_CONTENT_MODE=redacted` to retain bounded sanitized questions, model messages/responses, tool evidence, and diagnoses in MLflow. The general configuration defaults to `metadata`; repair traces remain metadata-only. No raw capture mode is offered. Local investigation history also retains bounded sanitized observations, including log excerpts, and reports. Redaction is best-effort, not a guarantee that every secret format or sensitive URL is detected. Use non-sensitive demo workloads/questions; keep MLflow local and review traces/history before sharing. SQLite is not immutable and local administrators can alter it.
- RBAC restricts verbs, namespaces, and names, not individual patch fields. Console/broker code enforces field restrictions; production needs compatible admission policies and network isolation. A compromised mutation component could exceed its application field allowlist within its named objects.
- Console sessions use local HTTP, not authenticated TLS, and share a demo-human actor rather than enterprise identity. Other laptop processes may reach localhost; protect the machine/key, lock the console, and rotate keys after public demonstrations.

## Real Slack scopes

Use a dedicated bot with only `chat:write` and `reactions:read`, invite it only to the approval channel, and rotate the token after a public demonstration. Never commit `.env` files, Kubernetes Secret output, the Local Slack URL, or `.kravel-local-state.env`.

## Production hardening before reuse

Add authenticated TLS ingress, network policy, encrypted persistent storage, centralized append-only audit export, signed image provenance, admission policy, per-user approval identity, and a reviewed fix catalog. Replace localhost anonymous dashboards with authenticated deployments.
