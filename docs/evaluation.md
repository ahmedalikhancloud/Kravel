# Local MLflow judges, without cloud keys

Kravel uses the real **MLflow 3.14.0 `mlflow.genai.evaluate` pipeline** and its 24 public built-in scorers: 21 LLM judges and 3 deterministic checks. A separate, serial evaluator uses an explicitly configured local OpenAI-compatible model. It has **no mounted Kubernetes identity, RBAC grants, kubectl tools, approval token, or ability to fix anything**. Scores are advisory and never gate or grant repair approval.

## Easiest demo

If Kravel is already prepared, upgrade without resetting labs, credentials, or trace history:

```bash
git pull --ff-only
bash demo/local/upgrade-evaluation.sh
bash demo/local/demo.sh --connect-only
```

A new install uses the usual `bash demo/local/prepare.sh`; it now includes the evaluator. Docker Desktop Kubernetes and Model Runner must be running. This needs no Groq, Codespaces, cloud account, or judge API key.

The Kubernetes manifests use `host.docker.internal:12434` for local inference, so Docker Desktop's localhost TCP access on port 12434 must be enabled. This route was reachable from the Windows Kubernetes pods where the usual `model-runner.docker.internal` route was not. A 502 on the reachable route means Docker's inference backend still needs recovery; changing hostnames alone does not fix that. The model API must not be exposed to your LAN.

MLflow serves evaluation artifacts over HTTP to its own PVC; the evaluator does not mount or write MLflow's filesystem. If an older experiment advertises a direct filesystem location, new requests/evaluations use **Kravel Guarded Debugger (HTTP artifacts)** instead. Old experiments, runs, and traces are preserved unchanged, not copied or deleted. UI links use each request's actual experiment ID. Ask Karl a new question after upgrading; historical requests in the old experiment are not automatically re-evaluated in the new one.

1. Ask Karl a question. Wait for the answer.
2. Under the completed investigation, expand **Evaluate Karl’s answer · local MLflow judges**.
3. Start **Quick evaluation**: RelevanceToQuery, Safety, Guidelines, PIIDetection, RegexMatch, ResponseLength. The first three make model calls; the latter three do not. RegexMatch checks for a non-whitespace response; ResponseLength checks 1–650 words. These simple checks are not correctness/security guarantees.
4. Watch each scorer's pending/running/completed/error status and duration. Expand a score for the model's rationale. Click **Open evaluation run in MLflow** or the scorer's **Inspect rubric, model request & result** link.
5. Use **All applicable scorers** when you have time. Add independent expected facts/reference text in the optional section to unlock reference-based scorers. Missing requirements are explicitly skipped, not scored as passing.

Bash alternative (latest completed answer in the current presentation session):

```bash
bash demo/local/evaluate.sh quick
bash demo/local/evaluate.sh all
# Optional: evaluate a specific completed investigation UUID.
bash demo/local/evaluate.sh quick YOUR_INVESTIGATION_UUID
```

Evaluation is opt-in and runs after the diagnostic answer, not inside its reported latency. One job and one scorer run at a time, with a bounded queue. The worker waits for Karl to be idle between scorers. An already-running judge cannot be preempted; a shared model can still delay a new chat turn. Use Quick for a smooth presentation. All can take several minutes and may hit small-model/context limitations.

## All 24 built-ins

| Group | Built-in scorers | Requirements / interpretation |
| --- | --- | --- |
| Answer quality | RelevanceToQuery, Completeness, Fluency, Safety | Recorded question/answer. Safety is about the response, not whether the input guardrail blocked an attack. |
| Product policy | Guidelines, ExpectationsGuidelines | Kravel's configured relevance, professionalism, evidence/uncertainty and human-approval guidelines; policy expectations are not factual ground truth. |
| Independent reference | Correctness, Equivalence | Correctness needs expected facts or an answer; Equivalence specifically needs an answer. Never derive the reference from the answer being judged. |
| Retrieved evidence | RetrievalGroundedness, RetrievalRelevance, RetrievalSufficiency | A real RETRIEVER span containing bounded Kubernetes context, not a pretend vector database. Sufficiency also needs independent facts/reference. Chunk-level feedback and precision can add more assessments than scorers. |
| Tool use | ToolCallCorrectness, ToolCallEfficiency | Actual recorded TOOL spans and tool schemas. Native judge tools can inspect the supplied MLflow trace only; they cannot call Kubernetes. |
| Summary task | Summarization | A question requesting a summary. Other questions explicitly skip this scorer. |
| Multi-turn | ConversationalGuidelines, ConversationalRoleAdherence, ConversationalSafety, ConversationalToolCallEfficiency, ConversationCompleteness, KnowledgeRetention, UserFrustration | At least two recorded, content-bearing turns in the same presentation session; up to the latest four, no future turns. Conversational tool efficiency also needs tool calls. Karl is currently stateless between questions: grouping traces does **not** add conversational memory. Retention judges can correctly report this limitation. Frustration uses categorical values, not a numeric confidence score. |
| Deterministic | PIIDetection, RegexMatch, ResponseLength | No LLM call. PII regexes can flag legitimate cluster IPs and miss other sensitive data; they are not certified DLP. |

Scorers are discovered from the pinned SDK's public exports. The four requiring configuration (Guidelines, ConversationalGuidelines, RegexMatch, ResponseLength) are explicitly configured. This avoids silently omitting them via `get_all_scorers()`. Safety and RetrievalRelevance have local implementations in this release even where the rolling documentation's availability note says otherwise.

## What you see in MLflow

**Kravel Guarded Debugger → Traces**: the original question, answer, guardrail checks/classifier outputs, retrieved context, tool reads and authorization decisions, complete tool schemas, graph routing, model messages/responses, finish reason, token counts when reported, timings and coverage gaps. A session ID groups current demo turns. Open the source trace's assessments to inspect native scores, rationale, scorer/model provenance and linked scorer traces.

**Kravel Guarded Debugger → Evaluation runs** (or the run link from Kravel): `Local judges · quick/all · ...` evaluation runs. Native metrics/results, independently supplied expectations, `answer-results.json`, `session-results.json` (when applicable), and `kravel-evaluation-report.json` with every skip/error/duration. Native evaluation may log the two result groups separately; the named artifacts preserve both. No diagnostic prediction is rerun during evaluation.

**Kravel Local Judges → Traces**: one manually sanitized EVALUATOR trace per scorer, with rubric, bounded question/answer or session IDs, expectations, duration, actual native Feedback and a nested `judge.local_inference` LLM span for each real judge call. Those nested spans show the actual SDK prompt/schema sent through the fixed local transport, response/rationale, finish reason and token usage. Cross-thread spans have explicit parents. Separate storage avoids MLflow's native evaluation cleanup removing custom scorer traces. The feedback metadata links each judge trace back to its source request/job.

Native session assessments are attached to the earliest evaluated turn in the session, not necessarily the answer you selected. Its ID appears in the job's `sessionTraceIds` and session scorer trace; inspect that turn or the evaluation run's session results. When both facts and a reference answer are supplied, Correctness/RetrievalSufficiency use the facts, while Equivalence uses the answer.

There is **no hidden chain-of-thought capture**. Returned judge explanations and graph decisions are observable outputs, not proof of the model's private reasoning or causal correctness.

## Same model or another local model?

Default: reuse your existing local Qwen 4B. This is simplest and free of API charges, but has self-judging bias and shared inference contention. For serious measurement, prefer a stronger **independent** local judge and compare its scores with human labels on a representative/adversarial dataset. Another model is not automatically objective or safety-trained.

To select a model you already downloaded into Model Runner:

```bash
export KRAVEL_JUDGE_MODEL='YOUR_ALREADY_DOWNLOADED_LOCAL_MODEL_ID'
bash demo/local/upgrade-evaluation.sh
```

No second model is downloaded automatically. A larger/second model uses additional RAM/VRAM and can make the laptop slower. Keep endpoints local; the worker rejects remote model hosts, embedded credentials and redirects. Missing/invalid model output, inference errors, schema failures or exhausted budgets become errors, not fake passing scores or cloud fallbacks. Each scorer has at most 3 transport calls, each at most 45 seconds and 800 output tokens, plus native local parsing/tool overhead. There is no strict whole-job wall-clock SLA.

## Trace depth and privacy

The local manifest now uses `KRAVEL_MLFLOW_CONTENT_MODE=redacted` and `KRAVEL_MLFLOW_TRACE_DETAIL=deep`: text fields up to 32,000 characters and sanitized span payloads up to 192,000, with labeled truncation. `standard` uses 6,000/24,000. The evaluator uses a separate derived view capped at 6,000 characters per text field for laptop inference, with root question/answer normalized and prior assessments removed. Original source span content is not rewritten. The job/trace explicitly describes this view, so scores are about the evidence actually available to the judge, not an unlimited cluster history.

No auth headers, raw upstream errors, stack locals, approval secrets, or unbounded raw capture are recorded. MLflow telemetry is disabled in the evaluator. Redaction is **best-effort**, not an enterprise DLP promise; use only synthetic/non-sensitive demo workloads and inspect traces before sharing. Model prompts containing trusted SDK rubric instructions are redacted but not quarantined; untrusted source evidence is already screened/redacted. `metadata` mode remains available for Karl but cannot evaluate content that was never recorded. Old traces without question/answer content cannot be retroactively recovered.

## Verification

```bash
python -m pip install -e '.[test,evaluation]'
python -m pytest
```

Tests exercise actual MLflow evaluation/feedback persistence and linked scorer/LLM spans, with deterministic checks and a local **fixture** HTTP model. Fixture scores verify transport/pipeline behavior only; they do not measure Qwen's quality. Tests also cover missing references/history, all public scorer configuration, local-only routing, redaction, bounded requests, and lack of execution authority.

References: [MLflow built-in scorers](https://mlflow.org/docs/latest/genai/eval-monitor/scorers/llm-judge/predefined/) and [supported judge model providers](https://mlflow.org/docs/latest/genai/eval-monitor/scorers/llm-judge/custom-judges/supported-models/). The installed 3.14.0 implementation, not the changing latest catalog, defines this demo's exact behavior.
