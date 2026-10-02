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

export function renderClusterPlans(container, run, api, submit) {
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
    button.disabled = run.status !== 'completed' || Boolean(run.payload?.approvalRequests?.length);
    button.addEventListener('click', async () => { button.disabled = true; try { await submit(run.id, plan.id); } finally { button.disabled = false; } });
    card.append(detail, button, el('small', 'Cluster-admin execution can affect any namespace, storage or security policy. Inspect every file and command. No change runs without your separate approval.'));
    container.append(card);
  }
}
