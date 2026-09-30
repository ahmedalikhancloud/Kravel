import { ClusterScene } from "./scene.js";
import { matchingResources, resourceId } from "./topology.mjs";

const $ = (selector) => document.querySelector(selector);
const elements = Object.fromEntries([...document.querySelectorAll("[id]")].map((element) => [element.id, element]));
const state = {cluster: null, selected: null, namespaceTool: false, activeTool: "get_resource", proposals: [], audit: [], kind: "all", busy: false, refreshing: false, pendingFixes: new Set(), readVersion: 0};
const labs = [
  {type: "oomkilled", name: "oom-demo", title: "Memory pressure", subtitle: "OOMKilled"},
  {type: "imagepullbackoff", name: "image-demo", title: "Image delivery", subtitle: "ImagePullBackOff"},
  {type: "crashloopbackoff", name: "crash-demo", title: "Process stability", subtitle: "CrashLoopBackOff"},
  {type: "bad_configmap", name: "config-demo", title: "Runtime configuration", subtitle: "Bad ConfigMap"},
];
const pluralKinds = {Pod: "pods", Deployment: "deployments", ReplicaSet: "replicasets", ConfigMap: "configmaps", Service: "services"};
const scene = new ClusterScene(elements.sceneCanvas, elements.resourceWorld, selectResource, (value) => { elements.cameraReadout.textContent = value; });
const logPodChoice = node("select"); logPodChoice.setAttribute("aria-label", "Pod for logs"); logPodChoice.hidden = true;
elements.logModeControl.before(logPodChoice);

function node(tag, className = "", text = "") {
  const value = document.createElement(tag); if (className) value.className = className; value.textContent = text; return value;
}
function namespace() { return elements.namespace.value; }
function selectedResource() { return state.cluster?.resources.find((resource) => resourceId(resource) === state.selected); }
function formatTime(value) { const date = new Date(value); return value && !Number.isNaN(date.valueOf()) ? new Intl.DateTimeFormat(undefined, {hour: "2-digit", minute: "2-digit", second: "2-digit"}).format(date) : "—"; }
function formatDuration(ms = 0) { return ms < 1000 ? `${ms.toFixed(0)}ms` : `${(ms / 1000).toFixed(2)}s`; }
function pretty(value) { return typeof value === "string" ? value : JSON.stringify(value, null, 2); }
function apiPath(path, params = {}) { const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== "" && value !== undefined)); return query.size ? `${path}?${query}` : path; }
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {"Content-Type": "application/json", ...options.headers}});
  const payload = await response.json().catch(() => ({error: `HTTP ${response.status}`}));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`); return payload;
}
let toastTimer;
function toast(message) { elements.toast.textContent = message; elements.toast.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => elements.toast.classList.remove("show"), 3400); }
async function copy(text) { try { await navigator.clipboard.writeText(text); toast("Copied to clipboard"); } catch { toast("Clipboard unavailable. Select the visible text and copy it manually."); } }
function showRail(view, focus = false) {
  elements.inspectorView.hidden = view !== "inspector"; elements.karlView.hidden = view !== "karl";
  [elements.inspectorTab, elements.karlTab].forEach((tab) => { const active = tab.id === `${view}Tab`; tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1; });
  document.body.classList.add("rail-open"); elements.openKarl.setAttribute("aria-expanded", "true");
  if (focus) (view === "karl" ? elements.chatInput : elements.inspectorTab).focus({preventScroll: true});
}
function closeRail() { document.body.classList.remove("rail-open"); elements.openKarl.setAttribute("aria-expanded", "false"); elements.openKarl.focus({preventScroll: true}); }
function setupTabs(container, select) {
  container.addEventListener("keydown", (event) => {
    const tabs = [...container.querySelectorAll('[role="tab"]')], index = tabs.indexOf(event.target); if (index < 0) return;
    let next; if (event.key === "ArrowRight") next = (index + 1) % tabs.length; else if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
    else if (event.key === "Home") next = 0; else if (event.key === "End") next = tabs.length - 1; else return;
    event.preventDefault(); tabs[next].focus(); select(tabs[next]);
  });
}

function selectResource(id, tool = "get_resource") {
  if (!state.cluster?.resources.some((resource) => resourceId(resource) === id)) return;
  state.selected = id; state.namespaceTool = false; elements.previousLogs.checked = false; renderInspector(); renderWorld(); renderIssues(); showRail("inspector"); runTool(tool);
}
function clearSelection(notify = false) {
  state.readController?.abort(); state.readVersion++; state.selected = null; state.namespaceTool = false;
  renderInspector(); scene.select(null); if (state.cluster) renderWorld();
  if (notify) toast("Selected resource no longer exists. The live topology has been updated.");
}
function connectedPods(id) {
  const reachable = new Set([id]), queue = [id];
  while (queue.length) {
    const current = queue.shift();
    for (const edge of state.cluster?.connections || []) if (edge.source === current && !reachable.has(edge.target)) { reachable.add(edge.target); queue.push(edge.target); }
  }
  return (state.cluster?.resources || []).filter((resource) => resource.kind === "Pod" && reachable.has(resourceId(resource))).sort((a, b) => Number(a.ready) - Number(b.ready) || a.name.localeCompare(b.name));
}
function renderInspector() {
  const resource = selectedResource(); elements.inspectorEmpty.hidden = Boolean(resource) || state.namespaceTool; elements.inspectorContent.hidden = !resource && !state.namespaceTool;
  elements.focusResource.disabled = !resource || scene.fallback; elements.focusSelected.disabled = !resource || Boolean(scene.fallback); elements.diagnoseSelected.disabled = !resource;
  if (!resource) return;
  elements.selectedKind.textContent = resource.kind; elements.selectedName.textContent = resource.name;
  elements.selectedStatus.textContent = resource.status; elements.selectedStatus.className = `health-tag ${resource.health}`;
  elements.selectedNamespace.textContent = `Namespace · ${resource.namespace}`;
  const edges = (state.cluster.connections || []).filter((edge) => edge.source === state.selected || edge.target === state.selected);
  elements.relatedCount.textContent = String(edges.length);
  elements.relatedResources.replaceChildren(...(edges.length ? edges.map((edge) => {
    const outgoing = edge.source === state.selected, id = outgoing ? edge.target : edge.source;
    const target = state.cluster.resources.find((item) => resourceId(item) === id);
    const button = node("button", "related-resource"); button.title = edge.evidence;
    button.append(node("span", `relation ${edge.relation}`, outgoing ? `${edge.relation} →` : `← ${edge.relation}`), node("b", "", target?.name || id), node("small", "", target?.kind || "resource"));
    button.addEventListener("click", () => { selectResource(id); scene.focus(id); }); return button;
  }) : [node("p", "panel-note", "No observed links for this object.")]));
  const pods = connectedPods(state.selected), previous = logPodChoice.value;
  logPodChoice.replaceChildren(...pods.map((pod) => { const option = node("option", "", pod.name); option.value = pod.name; return option; }));
  if (pods.some((pod) => pod.name === previous)) logPodChoice.value = previous;
}
function renderWorld() {
  const resources = state.cluster?.resources || [], connections = state.cluster?.connections || [];
  const filtered = matchingResources(resources, elements.resourceSearch.value, state.kind), ids = new Set(filtered.map(resourceId));
  scene.update(resources, connections, state.selected, ids);
  const links = connections.filter((edge) => ids.has(edge.source) && ids.has(edge.target)).length;
  elements.viewSummary.textContent = `${filtered.length}/${resources.length} objects · ${links} links`;
  elements.sceneEmpty.textContent = resources.length ? "No resources match. Clear the search or choose All." : "No supported resources found in this namespace.";
  elements.sceneEmpty.hidden = Boolean(filtered.length); elements.directoryCount.textContent = `(${filtered.length})`;
  // Keep focus intact when live polling updates existing keyboard-directory buttons.
  const existing = new Map([...elements.resourceDirectory.children].map((button) => [button.dataset.id, button]));
  const buttons = filtered.sort((a, b) => a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name)).map((resource) => {
    const id = resourceId(resource); let button = existing.get(id);
    if (!button) { button = node("button"); button.dataset.id = id; button.addEventListener("click", () => selectResource(id)); }
    button.textContent = `${resource.kind} · ${resource.name}`; button.setAttribute("aria-pressed", String(id === state.selected)); return button;
  });
  for (const child of [...elements.resourceDirectory.children]) if (!buttons.includes(child)) child.remove();
  buttons.forEach((button, index) => { if (elements.resourceDirectory.children[index] !== button) elements.resourceDirectory.insertBefore(button, elements.resourceDirectory.children[index] || null); });
}
function renderIssues() {
  const issues = state.cluster?.issues || []; elements.issueCount.textContent = String(issues.length);
  if (!issues.length) {
    const empty = node("div", "no-issues", "✓ No supported demo failures detected"); empty.append(node("small", "", "This is not a complete cluster health assessment.")); elements.issueList.replaceChildren(empty); return;
  }
  elements.issueList.replaceChildren(...issues.map((issue) => {
    const [kind, name] = issue.resource.split("/", 2), match = state.cluster.resources.find((resource) => resource.kind === kind && resource.name === name);
    const button = node("button", `issue ${issue.severity}${match && state.selected === resourceId(match) ? " selected" : ""}`);
    const heading = node("b"); heading.append(node("span", "", issue.title), node("span", "", issue.severity));
    button.append(heading, node("small", "", issue.resource), node("small", "", issue.evidence));
    button.addEventListener("click", () => { if (match) { selectResource(resourceId(match), "describe_resource"); scene.focus(resourceId(match)); } }); return button;
  }));
}
function renderWorkloads() {
  elements.workloadGrid.replaceChildren(...labs.map((lab) => {
    const resource = state.cluster?.resources.find((item) => item.kind === "Deployment" && item.name === lab.name), issue = state.cluster?.issues.find((item) => item.type === lab.type);
    const card = node("article", `workload ${issue?.severity || "healthy"}`);
    card.append(node("span", "kind", lab.title), node("h3", "", lab.name), node("span", "status", issue ? lab.subtitle : resource?.status || "Not installed"), node("p", "", issue?.evidence || (resource?.ready ? "Ready. Break this lab independently from your terminal." : "Waiting for a ready workload.")));
    const actions = node("div", "actions"), inspect = node("button", "", "Inspect"), propose = node("button", "propose", "Propose fix");
    inspect.disabled = !resource; inspect.addEventListener("click", () => selectResource(resourceId(resource), "describe_resource"));
    propose.disabled = !issue?.fixId || state.pendingFixes.has(issue?.fixId); propose.addEventListener("click", () => createProposal(issue.fixId)); actions.append(inspect, propose); card.append(actions); return card;
  }));
}
function renderCluster() {
  const cluster = state.cluster; if (!cluster) return;
  if (state.selected && !selectedResource()) clearSelection(true);
  elements.clusterTitle.textContent = cluster.namespace; elements.resourceCount.textContent = cluster.resources.length;
  elements.healthyCount.textContent = cluster.healthyPods; elements.podTotal.textContent = `of ${cluster.podCount} observed Pods`;
  elements.linkCount.textContent = `${cluster.connections?.length || 0} observed relationships`; elements.problemCount.textContent = cluster.issues.length;
  elements.healthSummary.textContent = cluster.issues.length ? "Investigation recommended" : "No supported failures detected";
  elements.observedAt.textContent = formatTime(cluster.observedAt); elements.observedAt.dateTime = cluster.observedAt;
  renderWorld(); renderInspector(); renderIssues(); renderWorkloads();
}

async function runTool(tool) {
  const resource = selectedResource(); if (!resource && !["get_pods", "get_events"].includes(tool)) return toast("Select a resource to inspect first.");
  state.readController?.abort(); const controller = new AbortController(); state.readController = controller; const version = ++state.readVersion;
  state.activeTool = tool; showRail("inspector");
  if ($(`#tab-${tool}`)) elements.toolOutput.setAttribute("aria-labelledby", `tab-${tool}`); else elements.toolOutput.removeAttribute("aria-labelledby");
  // Namespace-wide reads still have an evidence panel without a fabricated selected resource.
  if (!resource) { state.namespaceTool = true; elements.inspectorEmpty.hidden = true; elements.inspectorContent.hidden = false; elements.selectedKind.textContent = "NAMESPACE"; elements.selectedName.textContent = namespace(); elements.selectedStatus.textContent = "Read-only evidence"; elements.selectedStatus.className = "health-tag"; elements.selectedNamespace.textContent = "Namespace-wide query"; elements.relatedResources.replaceChildren(); elements.relatedCount.textContent = "0"; }
  $(".tool-tabs").querySelectorAll("button").forEach((button) => { const active = button.dataset.tool === tool; button.classList.toggle("active", active); button.setAttribute("aria-selected", String(active)); button.tabIndex = active ? 0 : -1; });
  const isLogs = tool === "pod_logs", pods = resource ? connectedPods(state.selected) : [];
  elements.logModeControl.hidden = !isLogs; logPodChoice.hidden = !isLogs || pods.length < 2;
  elements.toolOutput.textContent = "Collecting read-only evidence…"; elements.toolOutput.setAttribute("aria-busy", "true"); elements.toolTiming.textContent = "Loading…";
  const target = {namespace: namespace(), kind: pluralKinds[resource?.kind], name: resource?.name};
  const args = {get_pods: {namespace: namespace()}, get_resource: target, describe_resource: target, get_events: {namespace: namespace(), regarding_name: resource?.name || "", limit: 100}, pod_logs: {namespace: namespace(), pod: logPodChoice.value || pods[0]?.name, previous: elements.previousLogs.checked, tail_lines: 120}}[tool];
  if (isLogs && !args.pod) { elements.toolOutput.textContent = "No observed Pod connection for this resource. Select a Pod or a controller with an owned Pod to read logs."; elements.toolTiming.textContent = "No Pod selected"; elements.toolOutput.setAttribute("aria-busy", "false"); return; }
  try {
    const payload = await api("/v1/tools/run", {method: "POST", body: JSON.stringify({tool, namespace: namespace(), arguments: args}), signal: controller.signal});
    if (version !== state.readVersion) return;
    elements.toolOutput.textContent = isLogs ? (payload.result.logs || "No log lines returned.") : pretty(tool === "get_resource" ? payload.result.object : payload.result); elements.toolTiming.textContent = `${isLogs ? `${args.pod} · ${args.previous ? "previous" : "current"}` : tool.replaceAll("_", " ")} · ${formatDuration(payload.durationMs)}`;
    loadAudit();
  } catch (error) {
    if (error.name === "AbortError" || version !== state.readVersion) return;
    elements.toolOutput.textContent = `${error.message}${isLogs ? "\n\nPrevious logs exist only when a container has restarted. Try current logs or inspect Events for an image/startup failure." : ""}`; elements.toolTiming.textContent = "Read failed";
  } finally { if (version === state.readVersion) elements.toolOutput.setAttribute("aria-busy", "false"); }
}

// Small safe inline formatting, never HTML from a model, log, or Kubernetes object.
function messageContent(text) {
  const body = node("div", "message-body");
  String(text).split("\n").forEach((line) => {
    const paragraph = node("p");
    for (const part of line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)) {
      if (part.startsWith("**") && part.endsWith("**")) paragraph.append(node("strong", "", part.slice(2, -2)));
      else if (part.startsWith("`") && part.endsWith("`")) paragraph.append(node("code", "", part.slice(1, -1)));
      else paragraph.append(document.createTextNode(part));
    }
    body.append(paragraph);
  }); return body;
}
function addMessage(text, role = "system", title = "") {
  const message = node("div", `message ${role}`); if (title) message.append(node("h3", "", title)); message.append(messageContent(text)); elements.chat.append(message); elements.chat.scrollTop = elements.chat.scrollHeight; return message;
}
function setQuickActions(actions) {
  elements.quickActions.replaceChildren(...actions.map(({label, run}) => { const button = node("button", "", label); button.disabled = state.busy; button.addEventListener("click", run); return button; }));
}
function defaults() { setQuickActions([{label: "Inspect all Pods", run: () => { clearSelection(); runTool("get_pods"); }}, {label: "Recent Events", run: () => { clearSelection(); runTool("get_events"); }}, {label: "Diagnose failures", run: () => askKarl(`Diagnose every current failure in ${namespace()}. Use read-only evidence and separate facts from uncertainty.`)}]); }
async function askKarl(message) {
  if (state.busy || !message.trim()) return false;
  state.busy = true; showRail("karl"); elements.chatSend.disabled = true; elements.chatInput.readOnly = true; elements.chat.setAttribute("aria-busy", "true");
  addMessage(message, "user"); const waiting = addMessage("Gathering evidence with read-only tools…"); setQuickActions([]);
  const started = performance.now(), progress = setInterval(() => { elements.chatProgress.textContent = `Investigating · ${formatDuration(performance.now() - started)} elapsed`; }, 1000);
  try {
    const payload = await api("/v1/chat", {method: "POST", body: JSON.stringify({message, namespace: namespace()})});
    waiting.remove(); const card = addMessage(payload.report, "system", "Grounded diagnosis"), metrics = node("div", "metrics");
    metrics.append(node("span", "", `total ${formatDuration(payload.timings?.totalMs)}`), node("span", "", `Qwen ${formatDuration(payload.timings?.modelMs)}`), node("span", "", `${payload.tools?.length || 0} tools`), node("span", "", `trace ${payload.traceId ? payload.traceId.slice(0, 11) : "offline"}`)); card.append(metrics);
    state.busy = false; setQuickActions([...(payload.suggestedFixes || []).map((fix) => ({label: `Propose ${fix.title}`, run: () => createProposal(fix.id)})), {label: "Inspect evidence", run: () => { showRail("inspector"); if (selectedResource()) runTool("get_events"); else runTool("get_pods"); }}]);
    if (elements.chatInput.value.trim() === message) elements.chatInput.value = ""; await Promise.all([loadAudit(), loadProposals()]); return true;
  } catch (error) {
    waiting.remove(); addMessage(`Investigation failed: ${error.message}. Your draft is preserved; you can retry.`, "error"); state.busy = false; defaults(); return false;
  } finally {
    clearInterval(progress); state.busy = false; elements.chatSend.disabled = false; elements.chatInput.readOnly = false; elements.chat.setAttribute("aria-busy", "false"); elements.chatProgress.textContent = "Enter to send · Shift+Enter for a new line"; elements.chat.scrollTop = elements.chat.scrollHeight;
  }
}
async function createProposal(fixId) {
  if (state.pendingFixes.has(fixId)) return;
  state.pendingFixes.add(fixId); renderWorkloads();
  try {
    const proposal = await api("/v1/proposals", {method: "POST", body: JSON.stringify({fixId, namespace: namespace()})});
    toast(proposal.status === "pending" ? "Dry run passed. Human approval required." : `Proposal status: ${proposal.status}`);
    showRail("karl"); addMessage(`Prepared ${proposal.fix_id}. Status: ${proposal.status}. Inspect the command and dry-run in the approval inbox. Pending requests expire after five minutes; Karl cannot approve or execute them.`, "system", "Change request"); await Promise.all([loadProposals(), loadAudit()]);
  } catch (error) { showRail("karl"); addMessage(`Could not create a proposal: ${error.message}`, "error"); }
  finally { state.pendingFixes.delete(fixId); renderWorkloads(); }
}
function renderProposals() {
  elements.approvalCount.textContent = state.proposals.filter((proposal) => proposal.status === "pending").length;
  const open = new Set([...elements.proposalList.querySelectorAll("details[open]")].map((detail) => detail.dataset.id));
  if (!state.proposals.length) { elements.proposalList.replaceChildren(node("div", "empty-state", "No proposed changes. Investigate a failure, then prepare an allowlisted fix.")); return; }
  elements.proposalList.replaceChildren(...state.proposals.slice(0, 8).map((proposal) => {
    const card = node("article", `proposal ${proposal.status}`), summary = node("div");
    summary.append(node("h3", "", `${proposal.fix_id} · ${proposal.resource}`), node("span", "deadline", proposal.status === "pending" ? `expires ${formatTime(proposal.expires_at)}` : `${formatTime(proposal.created_at)}${proposal.approval_actor ? ` · ${proposal.approval_actor}` : ""}`));
    card.append(summary, node("span", "status-pill", proposal.status), node("code", "", proposal.command));
    const detail = node("details"); detail.dataset.id = proposal.id; detail.open = open.has(proposal.id); detail.append(node("summary", "", "Server dry-run output"), node("pre", "", pretty(proposal.dryRun))); card.append(detail);
    if (proposal.result?.error) card.append(node("p", "deadline", proposal.result.error)); return card;
  }));
}
function renderAudit() {
  elements.auditCount.textContent = state.audit.length;
  elements.auditList.replaceChildren(...(state.audit.length ? state.audit.slice(0, 60).map((entry) => { const row = node("div", "audit-row"); row.append(node("time", "", formatTime(entry.at)), node("code", "", entry.component), node("span", "", `${entry.action}${entry.resource ? ` · ${entry.resource}` : ""}`), node("span", `outcome ${entry.outcome}`, entry.outcome)); return row; }) : [node("div", "empty-state", "No audited actions yet.")]));
}
async function loadProposals() { try { state.proposals = (await api("/v1/proposals")).proposals || []; renderProposals(); } catch (error) { elements.proposalList.replaceChildren(node("div", "notice", `Approval service unavailable: ${error.message}. Approval state is unknown.`)); elements.approvalCount.textContent = "—"; } }
async function loadAudit() { try { state.audit = (await api("/v1/audit?limit=200")).entries || []; renderAudit(); } catch (error) { elements.auditList.replaceChildren(node("div", "notice", `Audit trail unavailable: ${error.message}`)); elements.auditCount.textContent = "—"; } }
async function refreshAll() {
  if (state.refreshing) return; state.refreshing = true; elements.refresh.disabled = true;
  await Promise.all([loadProposals(), loadAudit(), (async () => {
    try {
      state.cluster = await api(apiPath("/v1/cluster", {namespace: namespace()})); renderCluster(); elements.connectionStatus.textContent = "Live"; elements.connectionStatus.className = "live-status online"; elements.connectionWarning.hidden = true;
    } catch (error) {
      elements.connectionStatus.textContent = "Offline"; elements.connectionStatus.className = "live-status offline";
      elements.connectionWarning.textContent = `${state.cluster ? `Showing last known state from ${formatTime(state.cluster.observedAt)}. ` : "No cluster data yet. "}${error.message}. Check the local port-forward, then refresh.`; elements.connectionWarning.hidden = false;
      elements.healthSummary.textContent = "Live health unknown"; if (!state.cluster) { elements.viewSummary.textContent = "API unavailable"; elements.sceneEmpty.textContent = "The debugger is unreachable. Reconnect the local demo and refresh."; elements.sceneEmpty.hidden = false; }
    }
  })()]);
  state.refreshing = false; elements.refresh.disabled = false;
}

elements.refresh.addEventListener("click", refreshAll);
elements.namespace.addEventListener("change", () => { clearSelection(); state.cluster = null; refreshAll(); });
elements.resourceSearch.addEventListener("input", renderWorld);
elements.resourceFilters.addEventListener("click", (event) => { const button = event.target.closest("button[data-kind]"); if (!button) return; state.kind = button.dataset.kind; elements.resourceFilters.querySelectorAll("button").forEach((tab) => tab.setAttribute("aria-pressed", String(tab === button))); renderWorld(); });
elements.showConnections.addEventListener("change", () => scene.links(elements.showConnections.checked));
function mode(value) { scene.mode(value); elements.orbitMode.setAttribute("aria-pressed", String(value === "orbit")); elements.panMode.setAttribute("aria-pressed", String(value === "pan")); }
elements.orbitMode.addEventListener("click", () => mode("orbit")); elements.panMode.addEventListener("click", () => mode("pan"));
elements.zoomIn.addEventListener("click", () => scene.zoom(.8)); elements.zoomOut.addEventListener("click", () => scene.zoom(1.25)); elements.fitScene.addEventListener("click", () => scene.fit());
elements.focusResource.addEventListener("click", () => scene.focus(state.selected)); elements.focusSelected.addEventListener("click", () => scene.focus(state.selected));
function expand(enabled) { elements.explorer.classList.toggle("is-expanded", enabled); document.body.classList.toggle("explorer-expanded", enabled); elements.fullscreenScene.setAttribute("aria-pressed", String(enabled)); elements.fullscreenScene.setAttribute("aria-label", enabled ? "Collapse cluster explorer" : "Expand cluster explorer"); }
elements.fullscreenScene.addEventListener("click", () => expand(!elements.explorer.classList.contains("is-expanded")));
elements.clearSelection.addEventListener("click", () => clearSelection());
elements.openKarl.addEventListener("click", () => showRail("karl", true)); elements.closeRail.addEventListener("click", closeRail);
elements.inspectorTab.addEventListener("click", () => showRail("inspector")); elements.karlTab.addEventListener("click", () => showRail("karl"));
setupTabs($(".rail-tabs"), (tab) => showRail(tab === elements.inspectorTab ? "inspector" : "karl"));
$(".tool-tabs").addEventListener("click", (event) => { const button = event.target.closest("button[data-tool]"); if (button) runTool(button.dataset.tool); }); setupTabs($(".tool-tabs"), (tab) => runTool(tab.dataset.tool));
elements.previousLogs.addEventListener("change", () => runTool("pod_logs")); logPodChoice.addEventListener("change", () => runTool("pod_logs"));
elements.copyEvidence.addEventListener("click", () => copy(elements.toolOutput.textContent));
elements.diagnoseSelected.addEventListener("click", () => { const resource = selectedResource(); if (resource) askKarl(`Investigate ${resource.kind}/${resource.name} in ${resource.namespace}. Use read-only evidence and its related resources. State uncertainty and do not execute changes.`); });
elements.chatForm.addEventListener("submit", (event) => { event.preventDefault(); askKarl(elements.chatInput.value.trim()); });
elements.chatInput.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); elements.chatForm.requestSubmit(); } });
document.querySelectorAll("[data-copy]").forEach((button) => button.addEventListener("click", () => copy(button.dataset.copy)));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { if (elements.explorer.classList.contains("is-expanded")) { expand(false); elements.fullscreenScene.focus(); } else if (window.innerWidth <= 980 && document.body.classList.contains("rail-open")) closeRail(); }
  if (event.key === "/" && !event.ctrlKey && !event.metaKey && !["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) { event.preventDefault(); elements.resourceSearch.focus(); }
});
function disable3DControls() { [elements.orbitMode, elements.panMode, elements.zoomIn, elements.zoomOut, elements.fitScene, elements.focusResource, elements.focusSelected].forEach((button) => { button.disabled = true; }); }
if (scene.fallback) disable3DControls(); elements.clusterWorld.addEventListener("scene-fallback", disable3DControls);
$(".tool-tabs").querySelectorAll("button").forEach((tab) => { tab.id = `tab-${tab.dataset.tool}`; tab.setAttribute("aria-controls", "toolOutput"); }); elements.toolOutput.setAttribute("role", "tabpanel");
addMessage("Hey, I’m Karl. Pick an object to explore its evidence, or ask me to investigate. I can read Pods, logs, Events, manifests, and ConfigMaps. Changes always go through the separate approval broker and a human—not me."); defaults();
refreshAll(); setInterval(() => { if (!document.hidden) refreshAll(); }, 5000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshAll(); });
