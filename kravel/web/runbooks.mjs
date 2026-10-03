// Reference knowledge is deliberately separate from actionable repair authority.
import { mlflowUrl } from "./observability.mjs";
import { attachReviewSubmission } from "./plans.mjs";
function el(tag, text = "", className = "") { const item = document.createElement(tag); item.textContent = text; item.className = className; return item; }

function link(label, url) { const a = el("a", label, "trace-link"); a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; return a; }

export function retrievalSteps(result) {
  return [["bm25", "Keyword clues"], ["dense", "Meaning"], ["rrf", "Rank fusion"], ["cross_encoder", "Rerank"]].map(([id, label]) => ({id, label, active: result.timings ? Number.isFinite(result.timings[`${id}Ms`]) : id === "bm25" || result.ready, milliseconds: result.timings?.[`${id}Ms`]}));
}

function pipeline(result) {
  return retrievalSteps(result).map((step) => el("span", `${step.active ? "✓" : "○"} ${step.label}${Number.isFinite(step.milliseconds) ? ` · ${step.milliseconds.toFixed(1)} ms` : ""}`, step.active ? "rag-stage active" : "rag-stage"));
}

export function comparisonRows(summary = {}) {
  return ["bm25", "dense", "rrf", "reranked"].filter((key) => summary[key]).map((key) => ({key, ...summary[key]}));
}

function searchDetails(container, result) {
  container.replaceChildren(); container.hidden = false;
  const steps = el("div", "", "rag-pipeline"); steps.append(...pipeline(result)); container.append(steps);
  const trace = mlflowUrl("trace", result.experimentId, result.traceId); if (trace) container.append(link("See this search’s exact MLflow trace ↗", trace));
  const details = el("details", "", "learning-guide"); details.append(el("summary", "Why these references? · compare stage rankings"));
  const grid = el("div", "", "rag-rank-grid");
  for (const [name, ranking] of Object.entries(result.rankings || {})) {
    if (!ranking.length) continue;
    const card = el("article"); card.append(el("b", {bm25: "Keyword / BM25", dense: "Vector similarity", rrf: "Reciprocal rank fusion", reranked: "Cross-encoder"}[name]));
    const list = el("ol"); ranking.slice(0, 3).forEach((row) => list.append(el("li", row.title))); card.append(list); grid.append(card);
  }
  details.append(grid, el("p", "Ranks describe relevance, not root-cause confidence. Scores from different stages use different scales and are not probabilities.")); container.append(details);
}

function benchmarkResult(container, job) {
  container.replaceChildren(); if (!job.summary) return;
  const wrap = el("div", "", "rag-table-scroll"), table = el("table", "", "rag-table");
  const head = el("tr"); ["Pipeline", "Recall@3", "MRR@3", "nDCG@3", "Compute ms"].forEach((name) => head.append(el("th", name))); const thead = el("thead"); thead.append(head); table.append(thead);
  const tbody = el("tbody");
  comparisonRows(job.summary).forEach((row) => { const tr = el("tr"); [row.key, `${(row.recallAt3*100).toFixed(1)}%`, row.mrrAt3.toFixed(3), row.ndcgAt3.toFixed(3), row.latencyMs.toFixed(1)].forEach((value) => tr.append(el("td", value))); tbody.append(tr); });
  table.append(tbody); wrap.append(table); container.append(wrap, el("p", job.latencyMethod || ""));
  const nav = el("nav", "", "evaluation-links");
  for (const [view, id, label] of [["trace", job.traceId, "All retrieval spans ↗"], ["reports", job.mlflowRunId, "Per-query results & model provenance ↗"], ["traces", "", "Knowledge experiment ↗"]]) { const url = mlflowUrl(view, job.experimentId, id); if (url) nav.append(link(label, url)); }
  container.append(nav);
}

export function runbookCard(book, ask) {
  const card = el("details", "", "runbook-card"), summary = el("summary");
  summary.append(el("span", book.title), el("small", book.synthetic ? "Synthetic example" : book.group === "difficult" ? "Deep investigation" : book.group === "custom" ? "Team runbook" : "Common symptom")); card.append(summary);
  const body = el("div", "", "runbook-body");
  for (const [label, field] of [["1 · Establish the evidence", "evidenceRequired"], ["2 · Review the repair", "remediation"], ["3 · Verify the result", "verification"]]) body.append(el("b", label), el("p", book[field]));
  body.append(el("p", book.executionMode === "operator_led" ? "Operator-led: this case does not grant Karl any execution capability." : "A supported, evidence-based structured patch, server dry-run and separate human approval are required. Default mode does not need per-resource enrollment.", "runbook-boundary"));
  body.append(el("small", `Collection: ${book.collection || "kubernetes"} · version: ${book.version}`));
  if (book.provenance) { const ranks = Object.entries(book.provenance).filter(([,value]) => value).map(([stage,value]) => `${stage} #${value.rank}`); body.append(el("p", `Selected from ${ranks.join(" · ")}`, "runbook-boundary")); }
  if (book.matchedPassage) { const excerpt = el("details"); excerpt.append(el("summary", "Read the matched passage"), el("p", book.matchedPassage)); body.append(excerpt); }
  body.append(link(book.synthetic ? "Kubernetes mechanics reference ↗" : "Official reference ↗", book.source));
  if (ask) { const button = el("button", "Ask Karl to investigate this possibility"); button.addEventListener("click", () => ask(book)); body.append(button); }
  card.append(body); return card;
}

export async function installRunbooks({api, ask, elements}) {
  let books = [], request = 0;
  try { const catalog = await api("/v1/runbooks"); books = catalog.scenarios; elements.runbookStatus.textContent = `${catalog.counts.common} common + ${catalog.counts.difficult} difficult cases · curated coverage, not a universal ranking`; }
  catch (error) { elements.runbookStatus.textContent = `Runbook library unavailable: ${error.message}`; return; }
  const collections = [...new Set(books.map((book) => book.collection || "kubernetes"))].sort();
  collections.forEach((name) => { const option = el("option", name.replaceAll("-", " ")); option.value = name; elements.knowledgeCollection.append(option); });
  const render = (items) => {
    const filtered = items.filter((book) => (elements.runbookGroup.value === "all" || book.group === elements.runbookGroup.value) && (elements.knowledgeCollection.value === "all" || book.collection === elements.knowledgeCollection.value));
    elements.runbookResults.replaceChildren(...filtered.map((book) => runbookCard(book, ask)));
    elements.runbookCount.textContent = `${filtered.length} reference${filtered.length === 1 ? "" : "s"}`;
    if (!filtered.length) elements.runbookResults.append(el("p", "No matching reference. Karl can still inspect evidence and draft an unfamiliar repair within its safety boundaries."));
  };
  let matched = books, timer;
  const search = async () => {
    const version = ++request, query = elements.runbookSearch.value.trim();
    if (!query) { matched = books; render(matched); elements.ragSearchDetails.hidden = true; elements.runbookStatus.textContent = `${books.length} runbooks · reference knowledge, not execution authority`; return; }
    elements.runbookStatus.textContent = "Searching local reference knowledge…";
    try {
      const result = await api(`/v1/runbooks/search?q=${encodeURIComponent(query)}&collection=${encodeURIComponent(elements.knowledgeCollection.value)}`);
      if (version !== request) return;
      matched = result.hits; render(matched); searchDetails(elements.ragSearchDetails, result);
      elements.runbookStatus.textContent = `${result.mode === "hybrid_rrf" ? "BM25 + dense search + RRF" : "BM25 · no extra model required"}${result.reranked ? " + cross-encoder" : ""} · ${Math.round(result.totalMs)} ms${result.unavailable.length ? " · optional stage unavailable; lexical fallback used" : ""}. Matches are hypotheses, not diagnoses.`;
    } catch (error) { if (version === request) elements.runbookStatus.textContent = `Search unavailable: ${error.message}`; }
  };
  elements.runbookSearch.addEventListener("input", () => { request++; clearTimeout(timer); timer = setTimeout(search, 350); });
  elements.runbookGroup.addEventListener("change", () => render(matched)); render(books);
  elements.knowledgeCollection.addEventListener("change", search);
  document.querySelectorAll("[data-rag-query]").forEach((button) => button.addEventListener("click", () => { elements.runbookSearch.value = button.dataset.ragQuery; elements.runbookGroup.value = "all"; elements.knowledgeCollection.value = "all"; search(); }));
  let benchmarkTimer, jobId = "";
  const poll = async () => {
    try {
      const job = await api(`/v1/rag/jobs/${jobId}`);
      elements.ragBenchmarkStatus.textContent = `${job.status.replaceAll("_", " ")} · ${job.completedQueries}/${job.queryCount || "?"} synthetic queries${job.error ? ` · ${job.error}` : ""}`;
      benchmarkResult(elements.ragBenchmarkResults, job);
      if (["queued", "running"].includes(job.status)) { benchmarkTimer = setTimeout(poll, 1800); elements.ragBenchmark.disabled = true; }
      else { elements.ragBenchmark.disabled = false; }
    } catch (error) { elements.ragBenchmarkStatus.textContent = `Could not load saved results: ${error.message}`; elements.ragBenchmark.disabled = false; }
  };
  elements.ragBenchmark.addEventListener("click", async () => {
    elements.ragBenchmark.disabled = true; clearTimeout(benchmarkTimer); elements.ragBenchmarkResults.replaceChildren();
    elements.ragBenchmarkStatus.textContent = "Starting a CPU-only retrieval comparison. Kubernetes is unchanged.";
    try { const job = await api("/v1/rag/benchmark", {method: "POST", body: JSON.stringify({})}); jobId = job.id; poll(); }
    catch (error) { elements.ragBenchmarkStatus.textContent = `Comparison unavailable: ${error.message}`; elements.ragBenchmark.disabled = false; }
  });
  try {
    const status = await api("/v1/rag/status"); elements.ragPipeline.replaceChildren(...pipeline(status));
    elements.ragAvailability.textContent = status.ready ? `${status.documentCount} runbooks · free local CPU models · ${status.runtimeOffline ? "offline inference" : "check offline settings"} · no paid services` : status.notice;
    elements.ragBenchmark.disabled = !status.ready;
    elements.ragBenchmarkStatus.textContent = status.ready ? `${status.benchmarkQueries} synthetic questions, four retrieval variants. Click to measure; no model calls happen just by opening this panel.` : "Enable with: bash demo/local/enable-rag.sh";
    for (const [role, model] of Object.entries(status.models || {})) elements.ragModelDetails.append(el("b", `${role}: ${model.repo}`), el("p", `${model.license} · pinned revision ${model.revision} · ${model.artifactFiles} fingerprinted files`));
    if (status.latestJobId) { jobId = status.latestJobId; poll(); }
  } catch (error) { elements.ragAvailability.textContent = `Hybrid status unavailable: ${error.message}. Keyword search remains available.`; }
}

export function renderRunbookContext(container, payload) {
  container.replaceChildren(); const context = payload.runbooks;
  container.hidden = !context?.hits?.length; if (container.hidden) return;
  const group = el("details", "", "runbook-context");
  group.append(el("summary", `References Karl considered · ${context.hits.length} runbooks · ${Math.round(context.totalMs)} ms`), el("p", "Retrieved references are not observed causes. MLflow rag.* spans show each retrieval stage and latency."));
  context.hits.forEach((book) => group.append(runbookCard(book))); container.append(group);
  const trace = mlflowUrl("trace", context.experimentId, context.traceId); if (trace) group.append(link("Open the retrieval service trace ↗", trace));
}

export function renderDraftRepairs(container, run, submit, submissions = new Map()) {
  container.replaceChildren(); const drafts = run.payload?.disposition === "blocked" ? [] : run.payload?.draftRepairs || [];
  container.hidden = !drafts.length;
  for (const draft of drafts) {
    const card = el("article", "", "draft-card");
    card.append(el("span", "NEW REPAIR IDEA · NOT EXECUTED", "eyebrow"), el("h3", draft.resource), el("p", draft.draft.rationale));
    const preview = el("details"); preview.append(el("summary", "Inspect the exact proposed patch"), el("pre", JSON.stringify(draft.draft.patch, null, 2))); card.append(preview, el("p", draft.authorizationReason, "runbook-boundary"));
    const button = el("button", "Request server dry-run & human review");
    card.append(button, el("small", "This requests a preview and approval only. A separate human decision permits execution; expired or stale plans cannot run."));
    attachReviewSubmission(card, button, run, draft.id, submit, submissions);
    if (!draft.eligible) button.disabled = true;
    container.append(card);
  }
}
