# Enterprise patterns, demonstrated with synthetic data

Kravel is an independent side project. It is not a replica of an employer platform,
and does not demonstrate employer traffic, confidential architecture or achievements.
No resume is changed by this implementation.

The useful portfolio story is the progression from a RAG answer bot to an
observable support agent with live tools and separately approved execution.

| Pattern | What Kravel actually demonstrates |
|---|---|
| Knowledge-assisted support | BM25, local vectors, RRF, reranking, references and evidence requirements |
| Tool-using investigation | Read-only live Kubernetes diagnostics, bounded evidence and explicit gaps |
| Controlled remediation | Exact generated plans, server previews where available, five-minute independent human approval |
| AI observability | Local MLflow questions, guardrails, tool/model/retrieval spans, answer judges and retrieval regression metrics |
| Model delivery provenance | Immutable public model revisions, artifact hashes, safetensors-only offline loading |
| Knowledge refresh | Content/model-keyed vector caches, versioned custom collections, synthetic stale-index example |

Synthetic support examples include queue backlog, authentication audience mismatch,
dependency timeout budget and stale knowledge. Synthetic model-delivery examples
include artifact mismatch and model-load OOM. They are references, not a deployed
queue, identity provider or business application.

Important distinctions to explain honestly:

- Retrieved knowledge is not a live fact. A relevant runbook is a hypothesis;
  Karl must inspect tools and independent evidence before proposing a change.
- A model can plan, but the workflow—not the model—controls approval and execution.
- Collection filtering improves relevance; **it is not authorization**. A real
  enterprise deployment needs authenticated entitlements applied server-side.
- Local SQLite makes the laptop demo easy. It is not proof of scalable queues,
  distributed consistency, production traffic or a managed cloud deployment.
- The current executor is powerful and cluster-admin by user choice. Approval
  gates are not a substitute for production least privilege or policy enforcement.
- Curated regression queries and local LLM judges help detect regressions;
  neither certifies correctness, safety or production service-level objectives.

Optional side-project description (not inserted into any resume):

> Built Kravel, a local Kubernetes support agent combining hybrid runbook RAG,
> live diagnostic tools, MLflow tracing/evaluation, and independently human-approved
> execution of previewed Kubernetes plans through a 3D browser interface.
