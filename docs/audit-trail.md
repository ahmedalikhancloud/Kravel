# Audit trail

Kravel records its own decisions and actions; it does not ingest or retain Kubernetes API-server audit logs.

The debugger database records:

- cluster inspections;
- operator and Qwen read-only tool calls;
- investigation start, completion, failure, trace ID, and timings;
- forwarding of an allowlisted fix ID to the broker.
- actual evidence collection stages, sanitized local run history, and read-only recovery observations.

The separate broker database records:

- proposal creation and Kubernetes server dry-run result;
- optional Slack delivery and polling failures;
- approval, rejection, or five-minute timeout;
- approved execution result or error.
- revalidation and independently applied operations, including partial failure.

The separate operator database records sanitized manual read/preview commands, explicit applications, and refused requests. It never stores unlock keys, session cookies, or preview nonces. Browser console history is in-memory only.

Audit entries include timestamp, component, action, actor, resource, outcome, duration, trace ID, and structured details. They exclude ServiceAccount/Slack tokens, approval/console credentials, raw LLM prompts, and full log bodies. A separate local workflow table retains bounded sanitized investigation evidence, including log excerpts; treat these files as potentially sensitive. SQLite is not a tamper-proof audit sink.

The browser exposes a merged read-only view at `/v1/audit`. Each component exposes Prometheus counters at `/metrics`. The local manifest opts into bounded redacted investigation content in nested MLflow traces: question, selected resource, model request/response, tool observations, guardrail decisions, and final diagnosis. This is separate from metadata-only audit entries. Set `KRAVEL_MLFLOW_CONTENT_MODE=metadata` to omit this content from new traces; raw capture is unsupported. Existing traces are not backfilled. Repair review/execution/verification traces remain metadata-only.

For a real deployment, forward these records to an append-only external store before granting the broker any broader authority.
