# Audit trail

Kravel records its own decisions and actions; it does not ingest or retain Kubernetes API-server audit logs.

The debugger database records:

- cluster inspections;
- operator and Qwen read-only tool calls;
- investigation start, completion, failure, trace ID, and timings;
- forwarding of an allowlisted fix ID to the broker.

The separate broker database records:

- proposal creation and Kubernetes server dry-run result;
- optional Slack delivery and polling failures;
- approval, rejection, or five-minute timeout;
- approved execution result or error.

Audit entries include timestamp, component, action, actor, resource, outcome, duration, trace ID, and bounded structured details. They do not include ServiceAccount tokens, Slack tokens, the local approval token, raw LLM prompts, full log bodies, or arbitrary command input.

The browser exposes a merged read-only view at `/v1/audit`. Each component exposes Prometheus counters at `/metrics`. Investigation spans are sent to MLflow as metadata-only nested traces.

For a real deployment, forward these records to an append-only external store before granting the broker any broader authority.
