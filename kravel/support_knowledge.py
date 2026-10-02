"""Invented support examples. No employer documents, customer data or credentials."""
from .scenarios import VERSION, SOURCES


ROWS = [
    ("support_worker_backlog", "Synthetic support worker: queue backlog grows", "platform-support", "queue backlog consumer lag duplicate jobs retries saturation processing delays", "Check worker readiness, CPU throttling, backlog age and retry counters. Distinguish an upstream burst from a failing consumer.", "Review worker capacity and bounded retries. Preserve idempotency; do not purge the queue or replay jobs blindly.", "Oldest-message age and lag decline without duplicate processing.", "workload"),
    ("support_auth_config", "Synthetic integration API: authentication audience mismatch", "platform-support", "401 unauthorized OIDC audience issuer authentication invalid configuration integration API", "Compare the expected issuer/audience with non-secret application configuration and redacted error codes. A healthy Pod does not prove authentication works.", "Ask the owner for independently verified issuer/audience values; review a ConfigMap repair without collecting bearer credentials.", "A synthetic non-sensitive request succeeds, and invalid-audience requests remain denied.", "configuration"),
    ("support_dependency_timeout", "Synthetic support API: dependency timeout budget", "platform-support", "504 upstream timeout connection pool dependency latency deadline budget retries support API", "Inspect current timeout configuration, dependency response times, retry amplification and endpoint readiness.", "Review a measured timeout budget and bounded backoff rather than masking a failing dependency with unlimited retries.", "Synthetic request latency and error rate recover without increasing retry traffic.", "configuration"),
    ("support_stale_index", "Synthetic knowledge service: stale runbook index", "platform-support", "outdated answers stale document metadata index refresh content hash embeddings version knowledge", "Compare source content hashes, indexed versions and retrieval citations. A stale answer can be a retrieval problem even when the model is healthy.", "Rebuild only the reviewed knowledge collection, preserve model revision and record corpus hash. Recheck relevant queries before publishing.", "Citations use current versions and the regression query set passes.", "workload"),
    ("artifact_mismatch", "Synthetic inference service: model artifact mismatch", "model-delivery", "inference image model artifact checksum digest revision incompatible weights tokenizer startup failure", "Inspect image digest, model manifest and redacted startup errors. Verify the artifact checksum and tokenizer revision against the reviewed release.", "Use the independently approved matching image and immutable model artifact. Never download arbitrary executable model code or invent a production artifact URL.", "Artifact hashes match the release manifest and synthetic inference passes after rollout.", "workload"),
    ("inference_oom", "Synthetic inference service: model loading exceeds memory", "model-delivery", "model load OOMKilled memory weights resident memory inference worker killed cold start", "Confirm termination reason, model size, measured resident memory, cgroup limit and node allocatable capacity.", "Review a smaller compatible model or measured resource limits with the owner. Do not claim a larger memory limit fixes a leak.", "Cold start and a bounded synthetic load test finish without OOM or node pressure.", "workload"),
]


def support_runbooks():
    return [{"id": sid, "title": title, "collection": collection, "signals": signals,
        "evidenceRequired": evidence, "remediation": repair, "verification": verify,
        "category": category, "source": SOURCES.get(category, SOURCES["workload"]), "sourceLabel": "Synthetic example; reference covers Kubernetes mechanics only",
        "group": "synthetic", "synthetic": True, "version": VERSION,
        "restorableFields": [], "executionMode": "operator_led", "risk": "operator_review_required"}
        for sid, title, collection, signals, evidence, repair, verify, category in ROWS]
