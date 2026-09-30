const $ = (selector) => document.querySelector(selector);
const elements = {
  namespace: $("#namespace"), refresh: $("#refresh"), issueCount: $("#issueCount"), issueList: $("#issueList"),
  clusterTitle: $("#clusterTitle"), healthyCount: $("#healthyCount"), problemCount: $("#problemCount"), observedAt: $("#observedAt"),
  clusterWorld: $("#clusterWorld"), resourceWorld: $("#resourceWorld"), worldSelection: $("#worldSelection"), workloadGrid: $("#workloadGrid"),
  toolPanel: $("#toolPanel"), toolTabs: $(".tool-tabs"), toolOutput: $("#toolOutput"), toolTiming: $("#toolTiming"), proposalList: $("#proposalList"),
  auditList: $("#auditList"), auditCount: $("#auditCount"), chat: $("#chat"), quickActions: $("#quickActions"), chatForm: $("#chatForm"), chatInput: $("#chatInput"), toast: $("#toast"),
};

const state = {cluster: null, selectedIssue: null, selectedResource: null, proposals: [], audit: [], busy: false};
const labs = [
  {type: "oomkilled", name: "oom-demo", title: "Memory pressure", subtitle: "OOMKilled", fixId: "fix_oom_memory"},
  {type: "imagepullbackoff", name: "image-demo", title: "Image delivery", subtitle: "ImagePullBackOff", fixId: "fix_image_pull"},
  {type: "crashloopbackoff", name: "crash-demo", title: "Process stability", subtitle: "CrashLoopBackOff", fixId: "fix_crash_loop"},
  {type: "bad_configmap", name: "config-demo", title: "Runtime configuration", subtitle: "Bad ConfigMap", fixId: "fix_bad_configmap"},
];
const pluralKinds = {Pod: "pods", Deployment: "deployments", ConfigMap: "configmaps", Service: "services"};

function node(tag, className = "", text = "") {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== "") value.textContent = text;
  return value;
}

function namespace() { return elements.namespace.value; }
function formatTime(value) { return value ? new Intl.DateTimeFormat(undefined, {hour: "2-digit", minute: "2-digit", second: "2-digit"}).format(new Date(value)) : "—"; }
function formatDuration(ms) { return ms < 1000 ? `${ms.toFixed(0)}ms` : `${(ms / 1000).toFixed(2)}s`; }
function pretty(value) { return JSON.stringify(value, null, 2); }
function apiPath(path, params = {}) { const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== "" && value !== undefined)); return query.size ? `${path}?${query}` : path; }

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json", ...(options.headers || {})}, ...options});
  const payload = await response.json().catch(() => ({error: `HTTP ${response.status}`}));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function toast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  setTimeout(() => elements.toast.classList.remove("show"), 2200);
}

function issueFor(type) { return state.cluster?.issues?.find((issue) => issue.type === type); }
function sameResource(left, right) { return Boolean(left && right && left.kind === right.kind && left.name === right.name && left.namespace === right.namespace); }

function resourceShape(resource) {
  const object = node("span", `model-object model-${resource.kind.toLowerCase()}`);
  object.setAttribute("aria-hidden", "true");
  if (resource.kind === "Deployment") {
    object.append(node("i", "iso-cube layer-back"), node("i", "iso-cube layer-mid"), node("i", "iso-cube layer-front"));
  } else if (resource.kind === "ConfigMap") {
    object.append(node("i", "config-slab"), node("i", "config-line line-one"), node("i", "config-line line-two"), node("i", "config-line line-three"));
  } else if (resource.kind === "Service") {
    object.append(node("i", "service-ring"), node("i", "service-core"), node("i", "service-ray ray-a"), node("i", "service-ray ray-b"), node("i", "service-ray ray-c"));
  } else {
    object.append(node("i", "pod-shell"), node("i", "pod-window"), node("i", "pod-foot foot-a"), node("i", "pod-foot foot-b"));
  }
  return object;
}

function selectResource(resource, inspect = true) {
  state.selectedResource = {kind: resource.kind, name: resource.name, namespace: resource.namespace};
  state.selectedIssue = state.cluster?.issues?.find((issue) => issue.type === resource.issueType) || null;
  elements.worldSelection.textContent = `${resource.kind}/${resource.name}`;
  renderIssues();
  renderResourceWorld();
  if (inspect) runTool("get_resource", true);
}

function renderResourceWorld() {
  const resources = state.cluster?.resources || [];
  if (!resources.length) {
    elements.resourceWorld.replaceChildren(node("div", "world-empty", "No supported resources found"));
    return;
  }
  elements.resourceWorld.replaceChildren(...resources.map((resource, index) => {
    const button = node("button", `resource-model kind-${resource.kind.toLowerCase()} ${resource.health}${sameResource(state.selectedResource, resource) ? " selected" : ""}`);
    button.type = "button";
    button.style.setProperty("--spawn-delay", `${Math.min(index * 36, 420)}ms`);
    button.setAttribute("aria-label", `Inspect ${resource.kind} ${resource.name}, ${resource.status}`);
    const label = node("span", "resource-label");
    label.append(node("b", "", resource.name), node("span", "", `${resource.kind} · ${resource.status}`));
    button.append(resourceShape(resource), node("i", "health-beacon"), label);
    button.addEventListener("click", () => selectResource(resource));
    return button;
  }));
}

function renderIssues() {
  const issues = state.cluster?.issues || [];
  elements.issueCount.textContent = String(issues.length);
  if (!issues.length) {
    elements.issueList.replaceChildren(node("div", "no-issues", "✓ No demo failures detected"));
    return;
  }
  elements.issueList.replaceChildren(...issues.map((issue) => {
    const button = node("button", `issue ${issue.severity}${state.selectedIssue?.id === issue.id ? " selected" : ""}`);
    const heading = node("b");
    heading.append(node("span", "", issue.title), node("span", "", issue.severity));
    button.append(heading, node("small", "", issue.resource), node("small", "", issue.evidence));
    button.addEventListener("click", () => {
      state.selectedIssue = issue;
      const [kind, name] = issue.resource.split("/", 2);
      const match = state.cluster?.resources?.find((resource) => resource.kind === kind && resource.name === name)
        || state.cluster?.resources?.find((resource) => resource.issueType === issue.type);
      if (match) state.selectedResource = {kind: match.kind, name: match.name, namespace: match.namespace};
      renderIssues(); renderWorkloads(); renderResourceWorld(); runTool("describe_resource", true);
    });
    return button;
  }));
}

function renderWorkloads() {
  elements.workloadGrid.replaceChildren(...labs.map((lab) => {
    const issue = issueFor(lab.type);
    const severity = issue?.severity || "healthy";
    const card = node("article", `workload ${severity}`);
    card.append(node("span", "kind", "Deployment"), node("h3", "", lab.name), node("span", "status", issue ? lab.subtitle : "Healthy"), node("p", "", issue?.evidence || "Running and ready. Break this lab independently from the terminal."));
    const actions = node("div", "actions");
    const inspect = node("button", "", "Inspect");
    inspect.addEventListener("click", () => {
      state.selectedIssue = issue || {id: `healthy:${lab.name}`, type: lab.type, resource: `Deployment/${lab.name}`, title: lab.subtitle, fixId: lab.fixId};
      const resource = state.cluster?.resources?.find((item) => item.kind === "Deployment" && item.name === lab.name);
      if (resource) selectResource(resource, false);
      runTool("describe_resource", true);
    });
    const propose = node("button", "propose", "Propose fix");
    propose.disabled = !issue;
    propose.addEventListener("click", () => createProposal(issue.fixId));
    actions.append(inspect, propose);
    card.append(actions);
    return card;
  }));
}

function renderCluster() {
  const cluster = state.cluster;
  elements.clusterTitle.textContent = cluster ? `${cluster.resources.length} live objects in ${cluster.namespace}` : "Cluster unavailable";
  elements.healthyCount.textContent = cluster?.healthyPods ?? "—";
  elements.problemCount.textContent = cluster?.issues?.length ?? "—";
  elements.observedAt.textContent = formatTime(cluster?.observedAt);
  renderIssues(); renderResourceWorld(); renderWorkloads();
}

function selectedTarget() {
  if (state.selectedResource) {
    const {kind, name} = state.selectedResource;
    return {kind: pluralKinds[kind] || `${kind.toLowerCase()}s`, name, pod: kind === "Pod" ? name : "", regarding: name};
  }
  const issue = state.selectedIssue;
  if (!issue) return {kind: "deployments", name: "crash-demo", pod: "", regarding: ""};
  const [resourceKind, resourceName] = issue.resource.split("/");
  if (resourceKind === "Pod") return {kind: "pods", name: resourceName, pod: resourceName, regarding: resourceName};
  return {kind: pluralKinds[resourceKind] || `${resourceKind.toLowerCase()}s`, name: resourceName, pod: "", regarding: resourceName};
}

async function runTool(tool, focus = false) {
  const target = selectedTarget();
  const argumentsByTool = {
    get_pods: {namespace: namespace()},
    get_resource: {namespace: namespace(), kind: target.kind, name: target.name},
    get_events: {namespace: namespace(), regarding_name: target.regarding, limit: 100},
    describe_resource: {namespace: namespace(), kind: target.kind, name: target.name},
    pod_logs: {namespace: namespace(), pod: target.pod, previous: true, tail_lines: 120},
  };
  if (tool === "pod_logs" && !target.pod) {
    const podIssue = state.cluster?.issues?.find((issue) => issue.resource.startsWith("Pod/"));
    if (!podIssue) return toast("Select a failing Pod first");
    argumentsByTool.pod_logs.pod = podIssue.resource.split("/")[1];
  }
  elements.toolOutput.textContent = `$ ${tool.replaceAll("_", " ")}\ncollecting read-only evidence…`;
  elements.toolTabs.querySelectorAll("button").forEach((button) => button.classList.toggle("active", button.dataset.tool === tool));
  if (focus) elements.toolPanel.scrollIntoView({behavior: "smooth", block: "center"});
  try {
    const payload = await api("/v1/tools/run", {method: "POST", body: JSON.stringify({tool, namespace: namespace(), arguments: argumentsByTool[tool]})});
    elements.toolOutput.textContent = pretty(payload.result);
    elements.toolTiming.textContent = `${tool} · ${formatDuration(payload.durationMs)}`;
    await loadAudit();
  } catch (error) {
    elements.toolOutput.textContent = `ERROR: ${error.message}`;
    elements.toolTiming.textContent = "tool failed";
  }
}

function addMessage(text, role = "system", title = "") {
  const message = node("div", `message ${role}`);
  if (title) message.append(node("h3", "", title));
  message.append(node("div", "", text));
  elements.chat.append(message);
  elements.chat.scrollTop = elements.chat.scrollHeight;
  return message;
}

function setQuickActions(actions) {
  elements.quickActions.replaceChildren(...actions.map(({label, run}) => {
    const button = node("button", "", label);
    button.addEventListener("click", run);
    return button;
  }));
}

async function askKarl(message) {
  if (state.busy || !message.trim()) return;
  state.busy = true;
  addMessage(message, "user");
  const waiting = addMessage("Karl is using only get/list/describe/events/logs…", "system");
  setQuickActions([]);
  const started = performance.now();
  try {
    const payload = await api("/v1/chat", {method: "POST", body: JSON.stringify({message, namespace: namespace()})});
    waiting.remove();
    const card = addMessage(payload.report, "system", "Grounded diagnosis");
    const metrics = node("div", "metrics");
    metrics.append(node("span", "", `total ${formatDuration(payload.timings.totalMs)}`), node("span", "", `Qwen ${formatDuration(payload.timings.modelMs)}`), node("span", "", `${payload.tools.length} tools`), node("span", "", `trace ${payload.traceId ? payload.traceId.slice(0, 11) : "offline"}`));
    card.append(metrics);
    setQuickActions([
      ...payload.suggestedFixes.map((fix) => ({label: `Propose ${fix.title}`, run: () => createProposal(fix.id)})),
      {label: "Show Events", run: () => runTool("get_events", true)},
      {label: "Show previous logs", run: () => runTool("pod_logs", true)},
    ]);
    elements.toolTiming.textContent = `agent · ${formatDuration(performance.now() - started)}`;
    await Promise.all([loadAudit(), loadProposals()]);
  } catch (error) {
    waiting.remove();
    addMessage(`Investigation failed: ${error.message}`, "error");
  } finally {
    state.busy = false;
  }
}

async function createProposal(fixId) {
  try {
    const proposal = await api("/v1/proposals", {method: "POST", body: JSON.stringify({fixId, namespace: namespace()})});
    toast("Dry run passed. Approval request created.");
    addMessage(`Prepared ${proposal.fix_id}. The broker dry-run passed and is waiting up to five minutes for a human 👍 in Local Slack${proposal.slack_ts ? " or real Slack" : ""}.`, "system", "Approval required");
    await Promise.all([loadProposals(), loadAudit()]);
  } catch (error) {
    addMessage(`Could not create proposal: ${error.message}`, "error");
  }
}

function renderProposals() {
  if (!state.proposals.length) {
    elements.proposalList.replaceChildren(node("div", "empty-state", "No proposed changes. Diagnose a broken lab, then prepare an allowlisted fix."));
    return;
  }
  elements.proposalList.replaceChildren(...state.proposals.map((proposal) => {
    const card = node("article", `proposal ${proposal.status}`);
    const summary = node("div");
    summary.append(node("h3", "", `${proposal.fix_id} · ${proposal.resource}`), node("span", "deadline", proposal.status === "pending" ? `expires ${formatTime(proposal.expires_at)}` : `approved by ${proposal.approval_actor || "—"}`));
    card.append(summary, node("span", "status-pill", proposal.status), node("code", "", proposal.command));
    const details = node("details");
    details.append(node("summary", "", "Server dry-run output"), node("pre", "", pretty(proposal.dryRun)));
    card.append(details);
    return card;
  }));
}

function renderAudit() {
  elements.auditCount.textContent = String(state.audit.length);
  if (!state.audit.length) {
    elements.auditList.replaceChildren(node("div", "empty-state", "Audit trail is empty."));
    return;
  }
  elements.auditList.replaceChildren(...state.audit.slice(0, 80).map((entry) => {
    const row = node("div", "audit-row");
    row.append(node("time", "", formatTime(entry.at)), node("code", "", entry.component), node("span", "", `${entry.action}${entry.resource ? ` · ${entry.resource}` : ""}`), node("span", `outcome ${entry.outcome}`, entry.outcome));
    return row;
  }));
}

async function loadCluster() { state.cluster = await api(apiPath("/v1/cluster", {namespace: namespace()})); renderCluster(); }
async function loadProposals() { try { state.proposals = (await api("/v1/proposals")).proposals || []; renderProposals(); } catch { state.proposals = []; renderProposals(); } }
async function loadAudit() { try { state.audit = (await api("/v1/audit?limit=200")).entries || []; renderAudit(); } catch { state.audit = []; renderAudit(); } }

async function refreshAll() {
  elements.refresh.disabled = true;
  try { await Promise.all([loadCluster(), loadProposals(), loadAudit()]); }
  catch (error) { addMessage(`The debugger API is not ready: ${error.message}`, "error"); }
  finally { elements.refresh.disabled = false; }
}

if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
  elements.clusterWorld.addEventListener("pointermove", (event) => {
    const bounds = elements.clusterWorld.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width - .5;
    const y = (event.clientY - bounds.top) / bounds.height - .5;
    elements.clusterWorld.style.setProperty("--camera-y", `${x * 6}deg`);
    elements.clusterWorld.style.setProperty("--camera-x", `${58 - y * 4}deg`);
  });
  elements.clusterWorld.addEventListener("pointerleave", () => {
    elements.clusterWorld.style.setProperty("--camera-y", "0deg");
    elements.clusterWorld.style.setProperty("--camera-x", "58deg");
  });
}

elements.refresh.addEventListener("click", refreshAll);
elements.namespace.addEventListener("change", () => { state.selectedIssue = null; state.selectedResource = null; refreshAll(); });
elements.toolTabs.addEventListener("click", (event) => { const button = event.target.closest("button[data-tool]"); if (button) runTool(button.dataset.tool); });
elements.chatForm.addEventListener("submit", (event) => { event.preventDefault(); const value = elements.chatInput.value.trim(); elements.chatInput.value = ""; askKarl(value); });
document.querySelectorAll("[data-copy]").forEach((button) => button.addEventListener("click", async () => { await navigator.clipboard.writeText(button.dataset.copy); toast("Command copied to clipboard"); }));

addMessage("Hey, I’m Karl. I can inspect Pods, logs, Events, manifests, controllers, and ConfigMaps with a read-only ServiceAccount. If a demo fix is appropriate, I’ll hand a structured proposal to the separate approval broker—never execute it myself.", "system");
setQuickActions([
  {label: "Inspect all Pods", run: () => runTool("get_pods", true)},
  {label: "Show recent Events", run: () => runTool("get_events", true)},
  {label: "Diagnose current failures", run: () => askKarl("Diagnose every current failure in kravel-demo. Use Pods, Events, describes, and relevant previous logs. Separate evidence from uncertainty.")},
]);
refreshAll();
setInterval(() => { loadCluster().catch(() => {}); loadProposals(); loadAudit(); }, 5000);
