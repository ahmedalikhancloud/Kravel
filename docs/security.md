# Security notes

Kravel collects operational history that can contain sensitive metadata. Treat its database as production telemetry with a potentially higher blast radius than the live API because it preserves old values.

## Defaults in this repository

- `Secret.data` and `Secret.stringData` are replaced with `<redacted>` before persistence.
- `metadata.managedFields` is dropped to reduce size and accidental identity retention.
- The supplied ClusterRole does not grant access to Secret objects.
- The service is `ClusterIP`, runs as a non-root user, drops Linux capabilities, and uses a read-only root filesystem.
- API bearer authentication is supported through `KRAVEL_API_TOKEN` but is not automatically enabled.

## Before a real deployment

- Put TLS and authentication in front of the HTTP service. Configure an audit webhook credential rather than exposing an anonymous endpoint.
- Use a narrowly scoped ClusterRole and an explicit allow-list of watched resources.
- Encrypt the persistent volume and backups. Restrict database access to the Kravel service account.
- Review audit policy carefully. `RequestResponse` bodies can contain credentials, tokens, environment values, and custom-resource secrets.
- Treat Pod specs, ConfigMaps, audit usernames, source IPs, labels, and annotations as sensitive even when Kubernetes Secret values are redacted.
- Configure NetworkPolicies for the API server/audit adapter, Prometheus, DNS, and the chosen embedding endpoint only.
- If embeddings leave the cluster, redact or tokenize data first and document the processor and retention policy.
- Treat local-model inputs like data sent to any other processor. Tool results can include ConfigMaps, annotations, event notes, audit identities, and resource names even though Secret values are redacted.
- Treat Kubernetes fields as attacker-controlled prompt input. The input guardrails redact common credential forms, quarantine instruction-like text, cap payload size, and guard every Qwen tool result. These filters and model instructions are not a security boundary.
- Laya output is schema-validated. Qwen output is bounded, redacted, checked for evidence-quality signals, and stripped of direct mutation commands. A human must still review every claim and proposed action.
- The agent caller fixes cluster and namespace scope. Those fields are not shown in model tool schemas, model-supplied overrides are discarded, and out-of-scope resource keys are rejected.
- The local demo keeps Laya on a `ClusterIP`, uses Docker Model Runner's internal endpoint, and binds Grafana and MLflow port-forwards to `127.0.0.1`. Do not enable LAN-facing Model Runner TCP access.
- SQLite benchmark rows, Prometheus, and MLflow contain bounded numeric timings and labels only. They intentionally exclude credentials, endpoint URLs, evidence, prompts, generated answers, and remediation content.
- The runbook path executes read-only diagnostics only. The Qwen harness exposes no mutation tools. Both routes set `remediationExecuted: false` and require explicit human approval outside Kravel.
- Use HTTPS and authentication if any model or telemetry endpoint is moved off the local Docker/Kubernetes network.
- Rotate `KRAVEL_API_TOKEN`; do not put it directly in a checked-in manifest.
- Define deletion, retention, and legal-hold behavior before collecting regulated workloads.

This MVP sanitizes known Kubernetes Secret fields; it is not a general data-loss-prevention system. Custom resources and ordinary ConfigMaps can still carry secrets.
