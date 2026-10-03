function el(tag, text = '', className = '') { const n = document.createElement(tag); n.textContent = text; if (className) n.className = className; return n; }

export function readableReport(source = '') {
  // Some local models return a JSON envelope even when tools are disabled.
  // Display its prose only; request/planId fields NEVER become UI actions.
  // The original wire response remains in the stored report and MLflow trace.
  const paragraphs = String(source).split('\n\n');
  try {
    const envelope = JSON.parse(paragraphs[0]);
    if (envelope && !Array.isArray(envelope) && typeof envelope.summary === 'string' && envelope.summary.trim() && Object.keys(envelope).every(k => ['summary', 'request', 'planId'].includes(k))) {
      return [envelope.summary, ...paragraphs.slice(1)].join('\n\n');
    }
  } catch { /* Ordinary prose is already readable. */ }
  return String(source);
}

export function validationLabel(proposal) {
  if (!proposal.fix_id?.startsWith('plan-')) return 'Kubernetes server dry-run passed';
  const rows = proposal.dryRun || [];
  if (rows.some((r) => ['not_available', 'deferred'].includes(r.validation))) return 'Review required · some steps lack a passed server dry-run';
  return rows.some((r) => r.validation === 'passed') ? 'Supported mutation dry-runs passed · ordered checks follow approval' : 'Read/check plan · executes only after approval';
}

export function attachReviewSubmission(card, button, run, draftId, submit, submissions = new Map()) {
  const key = `${run.id}/${draftId}`;
  const recorded = run.payload?.approvalRequests?.find((p) => p.planId === draftId || p.draftId === draftId || (!p.planId && !p.draftId));
  const failure = run.payload?.reviewFailure;
  if (!submissions.has(key) && failure && (failure.planId === draftId || failure.draftId === draftId)) submissions.set(key, {status: 'failed', error: failure.error, validation: failure.validation, uncertain: !failure.confirmedRejected});
  const feedback = el('div', '', 'plan-feedback'); feedback.setAttribute('role', 'status'); feedback.setAttribute('aria-live', 'polite');
  const label = button.textContent;
  function paint() {
    const current = submissions.get(key);
    button.disabled = run.status !== 'completed' || Boolean(recorded) || ['validating', 'submitted', 'unknown'].includes(current?.status) || current?.uncertain === true;
    button.textContent = current?.status === 'validating' ? 'Validating with Kubernetes…' : recorded || current?.status === 'submitted' ? '✓ Sent for separate human review' : label;
    feedback.replaceChildren(); feedback.className = `plan-feedback ${current?.status || ''}`;
    if (recorded || current?.status === 'submitted') feedback.append(el('p', '✓ Review requested. Open the separate approval inbox; nothing runs until you approve.'));
    else if (current?.status === 'validating') feedback.append(el('p', 'Checking the exact plan. Please wait; no change is being applied.'));
    else if (current) {
      feedback.append(el('strong', current.uncertain ? 'Delivery is uncertain · check the approval inbox' : 'This plan needs a correction'), el('p', current.error), el('p', current.uncertain ? 'Do not resubmit until you have checked for an existing request. Nothing was executed by Karl.' : 'No approval request was created and no change was applied. Ask Karl to revise this plan using the validation details below.'));
      if (current.validation) { const details = el('details'); details.append(el('summary', 'Kubernetes validation details'), el('pre', JSON.stringify(current.validation, null, 2))); feedback.append(details); }
    }
  }
  button.addEventListener('click', async () => {
    if (button.disabled) return;
    submissions.set(key, {status: 'validating'}); paint();
    try { const proposal = await submit(run.id, draftId); submissions.set(key, {status: 'submitted', proposalId: proposal.id}); }
    catch (error) { submissions.set(key, {status: 'failed', error: error.message, validation: error.details?.validation, uncertain: error.details?.reviewCreated !== false}); }
    finally { paint(); }
  });
  card.append(feedback); paint();
}

export function renderClusterPlans(container, run, api, submit, submissions = new Map()) {
  const plans = run.payload?.disposition === 'blocked' ? [] : run.payload?.clusterPlans || [];
  if (!plans.length) return;
  container.hidden = false;
  for (const plan of plans) {
    const card = el('article', '', 'draft-card cluster-plan-card');
    card.append(el('span', 'CLUSTER PLAN · NOTHING EXECUTED', 'eyebrow'), el('h3', plan.resource), el('p', plan.summary), el('p', `${plan.stepCount} ordered steps · ${plan.files?.length || 0} generated files · exact hash-bound review`, 'runbook-boundary'));
    const detail = el('details'), body = el('div');
    detail.append(el('summary', 'Inspect commands & generated code'), body);
    let loading = false;
    detail.addEventListener('toggle', async () => {
      if (!detail.open || loading || body.children.length) return;
      loading = true; body.append(el('p', 'Loading the exact saved plan…'));
      try {
        const draft = await api(`/v1/cluster-plans/${encodeURIComponent(run.id)}/${encodeURIComponent(plan.id)}`);
        body.replaceChildren(el('p', `Immutable plan: ${draft.id}`, 'panel-note'), el('pre', draft.command));
        for (const [name, source] of Object.entries(draft.plan.files)) {
          const file = el('details'); file.append(el('summary', name), el('pre', source));
          const download = el('button', `Download ${name}`);
          download.addEventListener('click', () => { const url = URL.createObjectURL(new Blob([source], {type: 'text/plain;charset=utf-8'})), a = el('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); });
          file.append(download); body.append(file);
        }
      } catch (error) { body.replaceChildren(el('p', `Plan not available: ${error.message}`)); }
      finally { loading = false; }
    });
    const button = el('button', 'Validate & send exact plan for human approval');
    card.append(detail, button, el('small', 'Cluster-admin execution can affect any namespace, storage or security policy. Inspect every file and command. No change runs without your separate approval.'));
    attachReviewSubmission(card, button, run, plan.id, submit, submissions);
    container.append(card);
  }
}
