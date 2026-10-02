const params = new URLSearchParams(location.search);
const token = params.get("token") || sessionStorage.getItem("kravel-approval-token") || "";
if (token) sessionStorage.setItem("kravel-approval-token", token);
if (token) history.replaceState({}, "", location.pathname);
const messages = document.querySelector("#messages");
const warning = document.querySelector("#tokenWarning");
warning.hidden = Boolean(token);

function node(tag, className = "", text = "") {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== "") value.textContent = text;
  return value;
}

function time(value) { return new Intl.DateTimeFormat(undefined, {hour:"2-digit", minute:"2-digit", second:"2-digit"}).format(new Date(value)); }

async function api(path, options = {}) {
  const response = await fetch(path, {headers:{"Content-Type":"application/json", ...(options.headers || {})}, ...options});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

async function decide(proposal, action, event) {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await api(`/v1/proposals/${proposal.id}/${action}`, {method:"POST", headers:{"X-Kravel-Approval-Token":token}, body:JSON.stringify({actor:"local-slack-human", acceptUnvalidated: Boolean(button.closest("article").querySelector("input[data-risk-ack]")?.checked)})});
    await load();
  } catch (error) {
    button.disabled = false;
    alert(`Approval failed: ${error.message}`);
  }
}

function render(proposals) {
  const openDetails = new Set([...messages.querySelectorAll("details[open]")].map((details) => details.dataset.id));
  const acknowledged = new Set([...messages.querySelectorAll("input[data-risk-ack]:checked")].map((box) => box.dataset.riskAck));
  if (!proposals.length) {
    messages.replaceChildren(node("div", "empty", "No change requests yet. Ask Karl to prepare a fix from the Kravel debugger."));
    return;
  }
  messages.replaceChildren(...proposals.map((proposal) => {
    const article = node("article", "message");
    const avatar = node("div", "avatar");
    const image = node("img"); image.src = "/ui/karl-debugger.png"; image.alt = "Karl"; avatar.append(image);
    const content = node("div");
    const meta = node("div", "meta"); meta.append(node("b", "", "Karl  APP"), node("time", "", time(proposal.created_at)));
    const general = proposal.fix_id.startsWith("plan-"), limited = general && proposal.dryRun.some((r) => ["not_available", "deferred"].includes(r.validation));
    content.append(meta, node("h2", "", `Approval requested · ${proposal.fix_id}`), node("p", "", `${proposal.resource} · selected namespace ${proposal.namespace}${general ? " · commands may target other namespaces or the whole cluster" : ""}`), node("div", "command", proposal.command), node("span", "dryrun", limited ? "WARNING · some steps lack a passed server dry-run" : general ? "Review exact validation results below" : "Kubernetes server dry-run passed"));
    const details = node("details", "dryrun-details");
    details.dataset.id = proposal.id;
    details.open = openDetails.has(proposal.id);
    details.append(node("summary", "", "Inspect generated files, exact plan & validation outputs"), node("pre", "", JSON.stringify(proposal.dryRun, null, 2)));
    content.append(details);
    if (proposal.result?.error) content.append(node("p", "", proposal.result.error));
    if (proposal.status === "pending") {
      const actions = node("div", "actions");
      const approve = node("button", "approve", "👍 Approve"); approve.disabled = !token; approve.addEventListener("click", (event) => decide(proposal, "approve", event));
      if (limited) { const label = node("label", "risk-ack"), box = node("input"); box.type = "checkbox"; box.dataset.riskAck = proposal.id; box.checked = acknowledged.has(proposal.id); approve.disabled = !token || !box.checked; box.addEventListener("change", () => { approve.disabled = !token || !box.checked; }); label.append(box, node("span", "", "I reviewed every command and file, and accept the listed deferred/unavailable validation risks. This executor has cluster-admin permissions.")); content.append(label); }
      const reject = node("button", "reject", "✕ Reject"); reject.disabled = !token; reject.addEventListener("click", (event) => decide(proposal, "reject", event));
      const remaining = Math.max(0, Math.ceil((Date.parse(proposal.expires_at) - Date.now()) / 1000));
      actions.append(approve, reject, node("span", "countdown", `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2,"0")} remaining`));
      content.append(actions);
    } else {
      content.append(node("span", `status ${proposal.status}`, proposal.status), node("span", "countdown", proposal.approval_actor ? `by ${proposal.approval_actor}` : ""));
    }
    article.append(avatar, content);
    return article;
  }));
}

async function load() {
  try { const payload = await api("/v1/proposals"); const since = Date.parse(payload.session?.startedAt || ""); render((payload.proposals || []).filter((p) => ["pending", "approved", "executing"].includes(p.status) || !Number.isFinite(since) || Date.parse(p.created_at) >= since)); document.querySelector("#mode").textContent = payload.slackEnabled ? "SLACK + LOCAL" : "LOCAL WORKFLOW"; }
  catch (error) { messages.replaceChildren(node("div", "empty", `Broker unavailable: ${error.message}`)); }
}

load();
setInterval(load, 1000);
