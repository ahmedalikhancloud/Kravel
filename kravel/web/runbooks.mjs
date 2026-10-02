// Reference knowledge is deliberately separate from actionable repair authority.
function el(tag, text = "", className = "") { const item = document.createElement(tag); item.textContent = text; item.className = className; return item; }

export function runbookCard(book, ask) {
  const card = el("details", "", "runbook-card"), summary = el("summary");
  summary.append(el("span", book.title), el("small", book.group === "difficult" ? "Deep investigation" : book.group === "custom" ? "Team runbook" : "Common symptom")); card.append(summary);
  const body = el("div", "", "runbook-body");
  for (const [label, field] of [["1 · Establish the evidence", "evidenceRequired"], ["2 · Review the repair", "remediation"], ["3 · Verify the result", "verification"]]) body.append(el("b", label), el("p", book[field]));
  body.append(el("p", book.executionMode === "operator_led" ? "Operator-led: this case does not grant Karl any execution capability." : "An exact named resource/field profile, dry-run and separate human approval are required for execution.", "runbook-boundary"));
  const link = el("a", "Official reference ↗", "trace-link"); link.href = book.source; link.target = "_blank"; link.rel = "noopener noreferrer"; body.append(link);
  if (ask) { const button = el("button", "Ask Karl to investigate this possibility"); button.addEventListener("click", () => ask(book)); body.append(button); }
  card.append(body); return card;
}

export async function installRunbooks({api, ask, elements}) {
  let books = [], request = 0;
  try { const catalog = await api("/v1/runbooks"); books = catalog.scenarios; elements.runbookStatus.textContent = `${catalog.counts.common} common + ${catalog.counts.difficult} difficult cases · curated coverage, not a universal ranking`; }
  catch (error) { elements.runbookStatus.textContent = `Runbook library unavailable: ${error.message}`; return; }
  const render = (items) => {
    const filtered = items.filter((book) => elements.runbookGroup.value === "all" || book.group === elements.runbookGroup.value);
    elements.runbookResults.replaceChildren(...filtered.map((book) => runbookCard(book, ask)));
    elements.runbookCount.textContent = `${filtered.length} reference${filtered.length === 1 ? "" : "s"}`;
    if (!filtered.length) elements.runbookResults.append(el("p", "No matching reference. Karl can still inspect evidence and draft an unfamiliar repair within its safety boundaries."));
  };
  let matched = books, timer;
  const search = async () => {
    const version = ++request, query = elements.runbookSearch.value.trim();
    if (!query) { matched = books; render(matched); elements.runbookStatus.textContent = `${books.length} runbooks · reference knowledge, not execution authority`; return; }
    elements.runbookStatus.textContent = "Searching local reference knowledge…";
    try {
      const result = await api(`/v1/runbooks/search?q=${encodeURIComponent(query)}`);
      if (version !== request) return;
      matched = result.hits; render(matched);
      elements.runbookStatus.textContent = `${result.mode === "hybrid_rrf" ? "BM25 + dense search + RRF" : "BM25 · no extra model required"}${result.reranked ? " + cross-encoder" : ""} · ${Math.round(result.totalMs)} ms${result.unavailable.length ? " · optional stage unavailable; lexical fallback used" : ""}. Matches are hypotheses, not diagnoses.`;
    } catch (error) { if (version === request) elements.runbookStatus.textContent = `Search unavailable: ${error.message}`; }
  };
  elements.runbookSearch.addEventListener("input", () => { request++; clearTimeout(timer); timer = setTimeout(search, 350); });
  elements.runbookGroup.addEventListener("change", () => render(matched)); render(books);
}

export function renderRunbookContext(container, payload) {
  container.replaceChildren(); const context = payload.runbooks;
  container.hidden = !context?.hits?.length; if (container.hidden) return;
  const group = el("details", "", "runbook-context");
  group.append(el("summary", `References Karl considered · ${context.hits.length} runbooks · ${Math.round(context.totalMs)} ms`), el("p", "Retrieved references are not observed causes. MLflow rag.* spans show each retrieval stage and latency."));
  context.hits.forEach((book) => group.append(runbookCard(book))); container.append(group);
}

export function renderDraftRepairs(container, run, submit) {
  container.replaceChildren(); const drafts = run.payload?.disposition === "blocked" ? [] : run.payload?.draftRepairs || [];
  container.hidden = !drafts.length;
  for (const draft of drafts) {
    const card = el("article", "", "draft-card");
    card.append(el("span", "NEW REPAIR IDEA · NOT EXECUTED", "eyebrow"), el("h3", draft.resource), el("p", draft.draft.rationale));
    const preview = el("details"); preview.append(el("summary", "Inspect the exact proposed patch"), el("pre", JSON.stringify(draft.draft.patch, null, 2))); card.append(preview, el("p", draft.authorizationReason, "runbook-boundary"));
    const button = el("button", "Request server dry-run & human review");
    button.addEventListener("click", async () => { button.disabled = true; try { await submit(run.id, draft.id); } finally { button.disabled = false; } });
    button.disabled = run.status !== "completed"; card.append(button, el("small", "No change occurs here. Unenrolled repairs are rejected; only a separate human approval permits execution.")); container.append(card);
  }
}
