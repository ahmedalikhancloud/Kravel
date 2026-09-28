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
- Apply the same review to hosted-LLM calls. Tool results can include ConfigMaps, annotations, event notes, audit identities, and resource names even though Secret values are redacted.
- Treat Kubernetes fields as attacker-controlled prompt input. The harness instructs the model not to follow embedded instructions, caps tool output, and exposes no mutation tools, but model behavior is not a security boundary.
- The hosted-agent caller fixes the cluster and namespace scope. Those fields are not shown to the model, model-supplied overrides are discarded, and out-of-scope resource keys are rejected.
- Supply model credentials at runtime. The Killercoda helper streams the key through `kubectl exec` standard input and does not persist it in the Pod; production deployments should use an approved secret manager and egress policy.
- Use HTTPS for remote model endpoints. The harness rejects plain HTTP except for loopback development endpoints.
- Rotate `KRAVEL_API_TOKEN`; do not put it directly in a checked-in manifest.
- Define deletion, retention, and legal-hold behavior before collecting regulated workloads.

This MVP sanitizes known Kubernetes Secret fields; it is not a general data-loss-prevention system. Custom resources and ordinary ConfigMaps can still carry secrets.
