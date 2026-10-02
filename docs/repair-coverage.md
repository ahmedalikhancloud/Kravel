# Evidence-led repairs and Karl's field guide

Kravel now includes **25 common + 25 difficult production troubleshooting runbooks**.
These are curated examples, not a statistically established universal ranking.
They are also not 50 one-click production fixes: many hard failures involve data,
security or control-plane recovery that cannot safely be reduced to a generic patch.

## Start here (Bash / Git Bash)

Upgrade your existing installation without deleting labs, history, model caches or
credentials. Finish current investigations and approvals first:

```bash
bash demo/local/upgrade-debugger.sh
bash demo/local/demo.sh --connect-only
```

Open Kravel and expand **Karl's field guide**. Search by an error, symptom, or
question. Each reference has three parts: evidence to establish, repair guidance,
and checks needed to verify success. References are not incident detections.
An investigation displays its retrieved references and any newly drafted repair.

## How an unfamiliar problem can become a fix

1. Karl reads current objects, owner chains, Events and bounded logs.
2. Local runbook retrieval suggests competing hypotheses. Karl may consult a
   bounded public documentation page if there is an unresolved question.
3. If the intended correct value is known, Karl can call `draft_repair` with a
   structured patch and actual investigation evidence IDs. It cannot execute or
   submit approval through this tool. When you ask for a fix, Karl can additionally
   stage `request_repair_approval`; only after all guards pass does the application
   submit the exact plan to the broker for dry-run and Slack/Local Slack review.
   Unknown correct values must be requested,
   not guessed. A different application image is not a legitimate image-pull fix.
4. You inspect the patch and choose **Request server dry-run & human review**.
5. The separate executor rechecks deterministic kind/field policy and Kubernetes
   RBAC. Existing supported resources need no per-resource enrollment in default
   `approval_gated` mode. Unsupported fields/kinds/namespaces are rejected, even
   if the model says the change was approved. It runs server dry-run and records exact
   resource identity/spec and plan hashes.
6. A separate human approves in Local Slack/Slack within five minutes. Stale,
   revoked, interrupted, expired or unapproved plans never execute automatically.
7. The UI follows patch acceptance, controller rollout and independent read-only
   recovery checks. Readiness is not an end-to-end traffic test. ConfigMap-only
   changes do not claim consumer recovery; that needs operator verification.

Novel drafts support **existing Deployments, DaemonSets, Services and ConfigMaps in
`kravel-demo`**, with validated container image/command/args/resources/probes,
Pod scheduling/DNS fields, ConfigMap data and Service selector/ports. The executor
has namespace-scoped get/patch permissions; no object enrollment is required by
default. Existing container identity is verified before dry-run. StatefulSets are investigated
and visualized, but stateful/quorum mutations remain operator-led. There is no
arbitrary shell, secret access, RBAC editing, node mutation, Pod exec, deletion,
cluster-wide write, or policy bypass tool. The five existing lab fixes still work.

## Fix your manually created DaemonSet

For your `example-daemonset`, the observed pull Event reports an obsolete
schema1/`prettyjws` image format. A compatible replacement must be a verified
Fluentd image with the logging plugins/configuration you intend; do not use BusyBox
or an unverified image just to make the Pod green.

Ask Karl to investigate the DaemonSet and repair it using an independently verified
replacement Fluentd tag/digest. Include that intended value in your question.
Karl can draft the patch, request dry-run/review, and execute through its repair
service after your approval. Do not give Karl a thumbs-up in chat: approve in the
separate Slack/Local Slack inbox. No terminal enrollment is required.

If Karl only drafts, use **Request server dry-run & human review** on the draft.
If a correct replacement is unknown, Karl must ask for it instead of inventing one.

## Optional locked-down enrollment mode (operator configuration)

The original named-profile workflow remains available as `enrolled_only` mode.
This is **not** the default and is unnecessary for ordinary local demo repairs.
An operator must configure both worker and broker with that mode **and** narrow
`kravel-demo-fix-executor` to reviewed `resourceNames`. Changing the mode alone
does not reduce Kubernetes RBAC; roles are additive, not deny rules.

In that optional mode, set the variable below to your independently verified
replacement. This deliberately does not ship a guessed application image:

```bash
read -r -p 'Verified replacement Fluentd image/tag or digest: ' VERIFIED_FLUENTD_IMAGE
test -n "$VERIFIED_FLUENTD_IMAGE"
bash demo/local/enroll-repair.sh daemonsets example-daemonset legacy_image_format \
  --container fluentd --image "$VERIFIED_FLUENTD_IMAGE"
```

The optional helper only generates ignored local files. It does **not** apply anything.
Inspect `data/repair-enrollment.json`: it contains a policy ConfigMap in
`kravel-system`, plus a `Role`/`RoleBinding` granting **only get/patch of the named
DaemonSet** to the approval broker. The debugger gets no write permissions.

After reviewing the image, patch, namespace and `resourceNames`:

```bash
kubectl apply -f data/repair-enrollment.json
kubectl -n kravel-system rollout restart deployment/kravel deployment/kravel-approval-broker
kubectl -n kravel-system rollout status deployment/kravel --timeout=180s
kubectl -n kravel-system rollout status deployment/kravel-approval-broker --timeout=180s
bash demo/local/demo.sh --connect-only
```

Ask Karl to investigate the DaemonSet. Its failing Pod now stays connected to its
owner in the 3D map and evidence. A matching reviewed restore is offered, or Karl
can draft a different evidence-supported image value for this enrolled container.
Both require the same independent dry-run and human approval.

For other supported cases, enroll from a **reviewed, known-good object baseline**.
Do not snapshot broken memory/probe/configuration values as if they were healthy.
Multiple profiles are retained in the local policy file. Treat it as an operator
configuration artifact, keep it out of Git, and protect its directory. Enrollment
is intentionally not something Karl can perform.

In `enrolled_only` mode, remove custom profiles/grants without deleting workloads
or history. This does NOT revoke namespace-wide writes in `approval_gated` mode;
those require narrowing/removing the executor's base Role/RoleBinding as well:

```bash
kubectl -n kravel-demo delete rolebinding kravel-enrolled-repairs --ignore-not-found
kubectl -n kravel-demo delete role kravel-enrolled-repairs --ignore-not-found
kubectl -n kravel-system delete configmap kravel-repair-profiles --ignore-not-found
kubectl -n kravel-system rollout restart deployment/kravel deployment/kravel-approval-broker
```

Finish pending work before changing/revoking policies. Execution revalidates current
capabilities; RBAC revocation also prevents actual writes.

## Retrieval: free, local, incremental

Default: **BM25**, over 50 small versioned documents. No new model download, remote
database, vector service or cloud key. Exact error codes often work well here.

Optional: **BM25 + dense embeddings → RRF → small cross-encoder reranker**.
Dense retrieval helps paraphrases; RRF combines rankings without comparing raw
BM25 and cosine scores; a cross-encoder reranks a bounded shortlist. This can help,
but must be measured against BM25 instead of assumed to improve every query.

The optional `retrieval` Python extra / `Dockerfile.retrieval` installs Sentence
Transformers. Set `KRAVEL_RAG_EMBEDDING_PATH` and/or `KRAVEL_RAG_RERANKER_PATH` to
existing **absolute local model directories inside the container**, mounted
read-only by the operator. Models load on CPU with `local_files_only=True` and
`trust_remote_code=False`; no runtime hub downloads. This is opt-in: provision
compatible local models and their mounts before enabling it. It is not enabled by
the normal prepare/upgrade script, to preserve laptop memory and demo latency.
Missing optional models yield a visible lexical fallback, not a fake hybrid result.

Only curated **document vectors** are cached in local SQLite. Operator questions,
logs, credentials and cluster object snapshots are not added to this vector index.
For this corpus size, a separate vector database adds little value. Move to a local
dedicated index only when corpus size and measured retrieval costs justify it.

To add team knowledge without editing the built-in library, provide a read-only
JSON file via `KRAVEL_RUNBOOKS_PATH`. It is an array of records with exactly:

```json
{
  "id": "team-specific-connection-pool",
  "title": "Application connection pool saturation",
  "category": "workload",
  "signals": "connection pool saturation queue timeout",
  "evidenceRequired": "Correlate pool usage, queue depth and dependency latency.",
  "remediation": "The application owner reviews tested pool sizing and dependency limits.",
  "verification": "Queue depth and request latency recover under equivalent load.",
  "source": "https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/"
}
```

Custom records are validated and always **operator-led**. Adding knowledge cannot
add a capability, executable patch or RBAC permission. Reindexing happens when the
catalog hash changes. The current built-ins are in `kravel/scenarios.py`.

Public research is restricted to pre-reviewed HTTPS documentation pages on
`kubernetes.io/docs/`, `docs.docker.com`, and `sbert.net` (listed in
`kravel/research.py`). Arbitrary URL paths are rejected too, so a model cannot put
private data in a request path. DNS must resolve only to public IPs; connections
pin the validated address and retain TLS hostname verification. No redirects,
credentials, query parameters, private URLs or question/log uploads. Downloads and
tool calls are capped. References pass the same input/evidence guardrails and are
distinct from live observations. This is bounded documentation lookup, **not an
unrestricted web search engine**. Additional sources require explicit code/policy
review; private incident material is not sent to the internet.

## MLflow: inspect the actual path

From Kravel's request trace shortcut, inspect `rag.bm25`, optional `rag.dense`,
`rag.rrf`, optional `rag.cross_encoder`, and `rag.context`. The latter records
sanitized query, document IDs, source/version, content and actual/fallback mode.
`tool.search_runbooks`, `tool.fetch_reference`, `tool.draft_repair`,
`tool.request_repair_approval`, `repair.request_approval`, `tool.authorization`,
evidence guards, Qwen inference and output guards are also
separate spans. Metadata-only mode still omits content intentionally; local demo
redacted/deep mode exposes sanitized content. A draft never counts as execution.

Use a labeled query set and held-out paraphrases to evaluate Recall@k/MRR and
reranking quality before enabling heavier retrieval for every question. Retrieval
scores are not calibrated incident confidence or a substitute for live evidence.

## Coverage

**25 common:** OOMKilled; missing image/tag; registry authentication; registry
connectivity/TLS; crash startup command; invalid ConfigMap; missing ConfigMap/key;
missing Secret reference; init failure; liveness failure; readiness failure;
startup probe budget; CPU scheduling requests; memory scheduling requests; node
selector/affinity; untolerated taint; PVC Pending; failed mount; Service selector;
Service targetPort; no Ready endpoints; Pod DNS configuration; ingress backend;
Job backoff/deadline; stalled Deployment rollout.

**25 difficult:** obsolete image format; slow memory leak; CPU throttling;
ephemeral storage/inode eviction; CoreDNS saturation; DNS search amplification;
conntrack exhaustion; CNI MTU; asymmetric NetworkPolicy; stale Service dataplane;
CSI multi-attach; volume/node affinity; volume permissions; StatefulSet quorum;
webhook timeout/deadlock; webhook CA mismatch; API throttling; etcd latency/quorum;
certificate rotation; clock skew; PDB maintenance deadlock; HPA missing metrics;
topology spread; Pod IP exhaustion; GitOps repair reversion.

Not every case has an automatic detector or sufficient evidence in the current
namespace. Metric-dependent, node and control-plane cases explicitly need operator
evidence. Do not claim a healthy cluster solely because this bounded pass finds no
symptom. Existing practice buttons still simulate the five isolated labs, not 50
production failure injections.

References: [Kubernetes application debugging](https://kubernetes.io/docs/tasks/debug/debug-application/),
[Service debugging](https://kubernetes.io/docs/tasks/debug/debug-application/debug-service/),
[storage](https://kubernetes.io/docs/concepts/storage/persistent-volumes/),
[webhook safety](https://kubernetes.io/docs/concepts/cluster-administration/admission-webhooks-good-practices/),
[RRF](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion),
[retrieve and rerank](https://sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html).
