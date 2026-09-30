"use strict";

const $ = (selector) => document.querySelector(selector);
const elements = {
  namespace: $("#namespaceSelect"), collector: $("#collectorStatus"), refresh: $("#refreshButton"), live: $("#liveButton"),
  search: $("#resourceSearch"), filters: $("#kindFilters"), list: $("#resourceList"), resourceCount: $("#resourceCount"),
  title: $("#snapshotTitle"), selectedTime: $("#selectedTime"), banner: $("#timeBanner"), grid: $("#topologyGrid"),
  edges: $("#edgeLayer"), empty: $("#topologyEmpty"), healthy: $("#healthyCount"), warning: $("#warningCount"), broken: $("#brokenCount"),
  shardList: $("#shardList"), shardSummary: $("#shardSummary"), chatLog: $("#chatLog"), quickActions: $("#quickActions"),
  chatForm: $("#chatForm"), chatInput: $("#chatInput"), slider: $("#timeSlider"), timelineEvents: $("#timelineEvents"),
  timelineStart: $("#timelineStart"), timelineEnd: $("#timelineEnd"), timelineDelta: $("#timelineDelta"), eventStream: $("#eventStream"),
  baseline: $("#baselineButton"), play: $("#playButton"), drawer: $("#resourceDrawer"), drawerKind: $("#drawerKind"),
  drawerTitle: $("#drawerTitle"), drawerContent: $("#drawerContent"), closeDrawer: $("#closeDrawer"), askKarl: $("#askKarlResource"),
  copyManifest: $("#copyManifest"), toast: $("#toast"),
};

const state = {
  timeline: [], startMs: Date.now() - 300000, endMs: Date.now(), selectedMs: Date.now(), baselineMs: null,
  snapshot: [], graph: {nodes: [], edges: []}, diff: {changes: []}, warnings: [], shards: [],
  selectedKey: "", selectedObject: null, kind: "all", query: "", live: true, loading: false, replayTimer: null,
};

const KIND_GROUPS = {
  WORKLOADS: ["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Pod", "Job", "CronJob"],
  NETWORK: ["Service", "Endpoints", "EndpointSlice", "Ingress"],
  CONFIG: ["ConfigMap", "Secret", "ServiceAccount"],
  STORAGE: ["PersistentVolumeClaim", "PersistentVolume"],
};
const KIND_ICONS = {Deployment: "DP", Pod: "PO", ReplicaSet: "RS", Service: "SV", Endpoints: "EP", EndpointSlice: "ES", ConfigMap: "CM", Secret: "SC", ServiceAccount: "SA", PersistentVolumeClaim: "PV", Ingress: "IN", Job: "JB", CronJob: "CJ", StatefulSet: "ST", DaemonSet: "DS"};

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

function fmtTime(value, includeDate = false) {
  if (!value) return "—";
  const date = new Date(value);
  return new Intl.DateTimeFormat(undefined, includeDate
    ? {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"}
    : {hour: "2-digit", minute: "2-digit", second: "2-digit"}).format(date);
}

function iso(ms = state.selectedMs) { return new Date(ms).toISOString(); }
function namespace() { return elements.namespace.value; }
function resourceKey(object) {
  const meta = object.metadata || {};
  return `${object.apiVersion || "v1"}|${object.kind || "Unknown"}|${meta.namespace || "_cluster"}|${meta.name || "unknown"}`;
}
function labelFromKey(key) { const bits = String(key).split("|"); return bits.length >= 4 ? `${bits[1]}/${bits[3]}` : key; }

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"content-type": "application/json", ...(options.headers || {})}, ...options});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.message || payload.error || `Request failed (${response.status})`);
  return payload;
}

function queryString(values) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => { if (value !== "" && value !== undefined && value !== null) params.set(key, value); });
  return params.toString();
}

function resourceStatus(object) {
  const key = resourceKey(object);
  const meta = object.metadata || {};
  const status = object.status || {};
  const warning = state.warnings.find((event) => event.regardingName === meta.name || (object.kind === "Pod" && event.regardingName === meta.name));
  const waiting = (status.containerStatuses || []).some((entry) => entry.state && entry.state.waiting && /BackOff|Error|Pull/i.test(entry.state.waiting.reason || ""));
  const unscheduled = (status.conditions || []).some((entry) => entry.type === "PodScheduled" && entry.status === "False");
  if (warning || waiting || unscheduled) return "broken";
  if ((state.diff.changes || []).some((change) => change.resourceKey === key)) return "changed";
  return "healthy";
}

function groupFor(kind) {
  return Object.entries(KIND_GROUPS).find(([, kinds]) => kinds.includes(kind))?.[0] || "OTHER";
}

async function refreshHealth() {
  try {
    const ready = await api("/readyz");
    elements.collector.className = "collector-status ready";
    elements.collector.querySelector("b").textContent = `${ready.store.changes} changes stored`;
  } catch (error) {
    elements.collector.className = "collector-status error";
    elements.collector.querySelector("b").textContent = "Collector unavailable";
  }
}

async function loadTimeline() {
  const payload = await api(`/v1/timeline?${queryString({namespace: namespace(), limit: 1000})}`);
  state.timeline = payload.entries || [];
  state.startMs = payload.start ? Date.parse(payload.start) : Date.now() - 300000;
  state.endMs = payload.end ? Date.parse(payload.end) : Date.now();
  if (state.endMs <= state.startMs) state.startMs = state.endMs - 60000;
  state.baselineMs ??= state.startMs;
  if (state.live) state.selectedMs = state.endMs;
  elements.timelineStart.textContent = fmtTime(state.startMs, true);
  elements.timelineEnd.textContent = fmtTime(state.endMs, true);
  renderTimelinePins();
}

async function loadDemoWindow() {
  try {
    const snapshot = await api(`/v1/state/rewind?${queryString({timestamp: new Date(state.endMs).toISOString(), namespace: namespace(), kinds: "ConfigMap"})}`);
    const anchor = (snapshot.objects || []).find((item) => item.kind === "ConfigMap" && item.metadata?.name === "kravel-demo-window");
    const baseline = Date.parse(anchor?.data?.baselineAt || "");
    const incident = Date.parse(anchor?.data?.incidentAt || "");
    if (Number.isFinite(baseline) && Number.isFinite(incident)) {
      state.baselineMs = baseline;
      state.selectedMs = Math.min(state.endMs, incident);
      state.live = Math.abs(state.endMs - state.selectedMs) < 1000;
    }
  } catch (_error) {
    // Older captures do not include a demo anchor; the timeline remains usable.
  }
}

async function loadSnapshot() {
  if (state.loading) return;
  state.loading = true;
  const at = iso();
  const base = iso(state.baselineMs || state.startMs);
  try {
    const common = {timestamp: at, namespace: namespace()};
    const [graph, diff, context, incidents] = await Promise.all([
      api(`/v1/state/graph?${queryString(common)}`),
      api(`/v1/state/diff?${queryString({from: base, to: at, namespace: namespace()})}`),
      api(`/v1/context?${queryString({incidentAt: at, lookback: "5m", namespace: namespace(), limit: 100})}`),
      api(`/v1/incidents?${queryString({baselineAt: base, incidentAt: at, namespace: namespace()})}`),
    ]);
    state.snapshot = graph.objects || [];
    state.graph = graph.graph || {nodes: [], edges: []};
    state.diff = diff;
    state.warnings = (context.kubernetesEvents || []).filter((event) => event.type === "Warning");
    state.shards = incidents.shards || [];
    updateTimeChrome();
    renderAll();
  } catch (error) {
    addMessage(`I couldn’t reconstruct that moment: ${error.message}`, "error");
  } finally {
    state.loading = false;
  }
}

function updateTimeChrome() {
  const distance = Math.max(0, state.endMs - state.selectedMs);
  state.live = distance < 1000;
  elements.live.classList.toggle("active", state.live);
  elements.banner.classList.toggle("rewound", !state.live);
  elements.banner.querySelector("b").textContent = state.live ? "Live state" : `Rewound ${formatDuration(distance)} into the past`;
  elements.banner.querySelector("span:not(.rewind-glyph)").textContent = state.live ? "Karl is watching the cluster event stream." : `${state.diff.changeCount || 0} resources differ from the chosen baseline.`;
  elements.selectedTime.textContent = iso();
  elements.title.textContent = state.live ? "Cluster now" : `Cluster at ${fmtTime(state.selectedMs)}`;
  elements.timelineDelta.textContent = state.live ? "NOW" : `−${formatDuration(distance)}`;
  const range = state.endMs - state.startMs;
  elements.slider.value = range ? Math.round(((state.selectedMs - state.startMs) / range) * 1000) : 1000;
}

function formatDuration(ms) {
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}

function renderAll() {
  renderFilters();
  renderResourceList();
  renderTopology();
  renderShards();
  renderEventStream();
}

function renderFilters() {
  const kinds = [...new Set(state.snapshot.map((item) => item.kind))].sort();
  const values = ["all", ...kinds];
  elements.filters.replaceChildren(...values.map((kind) => {
    const button = node("button", `kind-filter${state.kind === kind ? " active" : ""}`, kind === "all" ? "All" : kind);
    button.type = "button";
    button.addEventListener("click", () => { state.kind = kind; renderFilters(); renderResourceList(); });
    return button;
  }));
}

function visibleResources() {
  return state.snapshot.filter((item) => {
    const meta = item.metadata || {};
    return (state.kind === "all" || item.kind === state.kind) && `${item.kind} ${meta.name}`.toLowerCase().includes(state.query.toLowerCase());
  });
}

function renderResourceList() {
  const resources = visibleResources();
  elements.resourceCount.textContent = String(resources.length);
  elements.list.replaceChildren(...resources.map((item) => {
    const key = resourceKey(item);
    const status = resourceStatus(item);
    const button = node("button", `resource-item${key === state.selectedKey ? " selected" : ""}`);
    button.type = "button";
    const icon = node("span", "resource-icon", KIND_ICONS[item.kind] || item.kind.slice(0, 2).toUpperCase());
    const text = node("span");
    text.append(node("b", "", item.metadata?.name || "unnamed"), node("small", "", item.kind));
    button.append(icon, text, node("i", `status-pip ${status}`));
    button.addEventListener("click", () => openResource(item));
    return button;
  }));
}

function renderTopology() {
  const groups = new Map();
  state.snapshot.forEach((item) => {
    const group = groupFor(item.kind);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(item);
  });
  const order = ["WORKLOADS", "NETWORK", "CONFIG", "STORAGE", "OTHER"].filter((name) => groups.has(name));
  elements.empty.classList.toggle("hidden", state.snapshot.length > 0);
  elements.grid.replaceChildren(...order.map((name) => {
    const lane = node("section", "lane");
    lane.append(node("div", "lane-title", name));
    groups.get(name).sort((a, b) => (a.metadata?.name || "").localeCompare(b.metadata?.name || "")).forEach((item) => {
      const key = resourceKey(item);
      const status = resourceStatus(item);
      const button = node("button", `resource-node ${status}${key === state.selectedKey ? " selected" : ""}`);
      button.type = "button";
      button.dataset.key = key;
      button.append(node("i", "node-icon", KIND_ICONS[item.kind] || item.kind.slice(0, 2).toUpperCase()), node("b", "", item.metadata?.name || "unnamed"), node("span", "", `${item.kind} · ${status}`));
      button.addEventListener("click", () => openResource(item));
      lane.append(button);
    });
    return lane;
  }));
  const statuses = state.snapshot.map(resourceStatus);
  elements.healthy.textContent = statuses.filter((item) => item === "healthy").length;
  elements.warning.textContent = statuses.filter((item) => item === "changed").length;
  elements.broken.textContent = statuses.filter((item) => item === "broken").length;
  requestAnimationFrame(drawEdges);
}

function drawEdges() {
  const stage = $("#topologyStage");
  const stageRect = stage.getBoundingClientRect();
  const width = Math.max(stage.scrollWidth, stage.clientWidth);
  const height = Math.max(stage.scrollHeight, stage.clientHeight);
  elements.edges.setAttribute("viewBox", `0 0 ${width} ${height}`);
  elements.edges.replaceChildren(...(state.graph.edges || []).map((edge) => {
    const from = elements.grid.querySelector(`[data-key="${CSS.escape(edge.from)}"]`);
    const to = elements.grid.querySelector(`[data-key="${CSS.escape(edge.to)}"]`);
    if (!from || !to) return null;
    const a = from.getBoundingClientRect(), b = to.getBoundingClientRect();
    const x1 = a.left - stageRect.left + stage.scrollLeft + a.width / 2;
    const y1 = a.top - stageRect.top + stage.scrollTop + a.height / 2;
    const x2 = b.left - stageRect.left + stage.scrollLeft + b.width / 2;
    const y2 = b.top - stageRect.top + stage.scrollTop + b.height / 2;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const bend = Math.max(30, Math.abs(x2 - x1) * .35);
    path.setAttribute("d", `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`);
    path.setAttribute("class", `edge-line${edge.from === state.selectedKey || edge.to === state.selectedKey ? " highlight" : ""}`);
    return path;
  }).filter(Boolean));
}

function renderTimelinePins() {
  const span = state.endMs - state.startMs;
  const recent = state.timeline.slice(-250);
  elements.timelineEvents.replaceChildren(...recent.map((entry) => {
    const pin = node("i", `event-pin${entry.severity === "warning" ? " warning" : ""}`);
    pin.style.left = `${span ? ((Date.parse(entry.at) - state.startMs) / span) * 100 : 100}%`;
    return pin;
  }));
}

function renderEventStream() {
  const entries = state.timeline.filter((item) => Date.parse(item.at) <= state.selectedMs).slice(-7).reverse();
  elements.eventStream.replaceChildren(...entries.map((entry) => {
    const card = node("article", `event-card${entry.severity === "warning" ? " warning" : ""}`);
    const body = node("div");
    body.append(node("b", "", entry.title || entry.action), node("span", "", `${fmtTime(entry.at)} · ${entry.entryType}`));
    card.append(node("i"), body);
    card.title = entry.note || entry.title || "";
    card.addEventListener("click", () => {
      const object = state.snapshot.find((item) => item.metadata?.name === entry.name && item.kind === entry.kind);
      if (object) openResource(object);
    });
    return card;
  }));
}

function renderShards(shards = state.shards) {
  elements.shardSummary.textContent = shards.length ? `${shards.length} evidence shard${shards.length === 1 ? "" : "s"} in this window` : "No causal signatures in this window";
  elements.shardList.replaceChildren(...shards.map((shard) => {
    const deterministic = shard.source === "kubernetes_signal";
    const card = node("article", `shard-card ${deterministic ? "signal" : "laya"}`);
    card.append(node("b", "", shard.diagnosis.replaceAll("_", " ")), node("span", "", deterministic ? `K8s signal · ${Math.round((shard.score || 0) * 100)}%` : "Laya hypothesis"));
    return card;
  }));
}

function toYaml(value, depth = 0) {
  const pad = "  ".repeat(depth);
  if (value === null) return "null";
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  if (typeof value === "string") return /[:#\n{}\[\],&*!|>'"%@`]|^\s|\s$/.test(value) ? JSON.stringify(value) : value;
  if (Array.isArray(value)) return value.length ? value.map((entry) => typeof entry === "object" && entry !== null ? `${pad}-\n${toYaml(entry, depth + 1)}` : `${pad}- ${toYaml(entry, 0)}`).join("\n") : "[]";
  const keys = Object.keys(value || {});
  if (!keys.length) return "{}";
  return keys.map((key) => {
    const entry = value[key];
    return typeof entry === "object" && entry !== null ? `${pad}${key}:\n${toYaml(entry, depth + 1)}` : `${pad}${key}: ${toYaml(entry, 0)}`;
  }).join("\n");
}

async function openResource(object) {
  state.selectedObject = object;
  state.selectedKey = resourceKey(object);
  elements.drawerKind.textContent = object.kind || "RESOURCE";
  elements.drawerTitle.textContent = object.metadata?.name || "Manifest";
  renderResourceList(); renderTopology();
  await selectDrawerTab("manifest");
  if (!elements.drawer.open) elements.drawer.showModal();
}

async function selectDrawerTab(tab) {
  document.querySelectorAll(".drawer-tabs button").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.tab === tab)));
  elements.drawerContent.replaceChildren();
  if (!state.selectedObject) return;
  if (tab === "manifest") {
    elements.drawerContent.append(node("pre", "manifest-code", toYaml(state.selectedObject)));
  } else if (tab === "relations") {
    const payload = await api(`/v1/state/trace?${queryString({timestamp: iso(), resourceKey: state.selectedKey, maxDepth: 2})}`);
    const list = node("div", "relation-list");
    if (!payload.edges.length) list.append(node("div", "relation-card", "No relationships were reconstructed for this object."));
    payload.edges.forEach((edge) => {
      const card = node("div", "relation-card");
      card.append(node("b", "", edge.type.replaceAll("-", " ")), node("span", "", `${labelFromKey(edge.from)} → ${labelFromKey(edge.to)}`));
      list.append(card);
    });
    elements.drawerContent.append(list);
  } else {
    const relevant = (state.diff.changes || []).filter((change) => change.resourceKey === state.selectedKey);
    const list = node("div", "change-list");
    if (!relevant.length) list.append(node("div", "change-card", "No change from the chosen baseline to this moment."));
    relevant.forEach((change) => {
      const card = node("div", "change-card");
      card.append(node("b", "", `${change.changeType} · ${change.changedPaths.length} paths`), node("span", "", change.changedPaths.join(", ")));
      list.append(card);
    });
    elements.drawerContent.append(list);
  }
}

function addMessage(text, type = "system", meta = "") {
  const message = node("div", `message ${type}`, text);
  if (meta) message.append(node("div", "message-meta", meta));
  elements.chatLog.append(message);
  elements.chatLog.scrollTop = elements.chatLog.scrollHeight;
  return message;
}

function setQuickActions(options = [], approval = false) {
  elements.quickActions.replaceChildren(...options.map((label) => {
    const button = node("button", `quick-chip${approval && /approve/i.test(label) ? " approve" : ""}`, label);
    button.type = "button";
    button.addEventListener("click", () => handleQuickAction(label));
    return button;
  }));
}

async function askKarl(payload, userText = "") {
  if (userText) addMessage(userText, "user");
  const thinking = addMessage("Karl is reading the reconstructed state…", "system");
  thinking.classList.add("thinking");
  try {
    const response = await api("/v1/karl/chat", {method: "POST", body: JSON.stringify({namespace: namespace(), at: iso(), baselineAt: iso(state.baselineMs || state.startMs), resourceKey: state.selectedKey, ...payload})});
    thinking.remove();
    addMessage(response.message, "system", response.kind === "reconstruction" ? `${response.diff.changeCount} changed resources · ${response.warnings.length} warnings` : "");
    setQuickActions(response.options || [], response.kind === "approval_request");
    if (response.resource) {
      const existing = state.snapshot.find((item) => resourceKey(item) === state.selectedKey);
      if (existing) openResource(existing);
    }
    return response;
  } catch (error) {
    thinking.remove(); addMessage(error.message, "error"); return null;
  }
}

async function runAnalysis() {
  setQuickActions([]);
  const started = performance.now();
  const progress = addMessage("Approved. The local guarded investigation is running; no cluster mutation is permitted.", "system");
  progress.classList.add("thinking");
  const timer = setInterval(() => { progress.textContent = `Guarded investigation in progress · ${((performance.now() - started) / 1000).toFixed(1)}s`; }, 250);
  try {
    const payload = await api("/v1/karl/analyze", {method: "POST", body: JSON.stringify({approved: true, baselineAt: iso(state.baselineMs || state.startMs), incidentAt: iso(), namespace: namespace(), scenario: "karl_ui"})});
    clearInterval(timer); progress.remove();
    const result = payload.result;
    const card = node("article", "analysis-card");
    card.append(node("h3", "", `${result.decision.replaceAll("_", " ")} · ${result.route.replaceAll("_", " ")}`));
    card.append(node("p", "", result.qwen?.report || `High-confidence read-only runbook selected. ${result.predefinedAutomation?.matchedChanges?.length || 0} matching changes were verified.`));
    const timings = node("dl");
    [["Laya", result.stageMetrics.laya_inference || 0], ["Qwen", result.stageMetrics.qwen_inference || 0], ["Trace flush", result.stageMetrics.mlflow_trace_flush || 0], ["Total", payload.totalMs]].forEach(([label, value]) => { timings.append(node("dt", "", label), node("dd", "", `${(value / 1000).toFixed(2)}s`)); });
    card.append(timings);
    elements.chatLog.append(card);
    elements.chatLog.scrollTop = elements.chatLog.scrollHeight;
    renderShards(result.evidence.shards || []);
    addMessage(`Human review is still required. Remediation executed: ${String(result.proposal.remediationExecuted)}. MLflow trace ${payload.traceId || "unavailable"}.`, "system");
    setQuickActions(["Show changes in window", "Return to live state"]);
  } catch (error) {
    clearInterval(timer); progress.remove(); addMessage(`Investigation failed safely: ${error.message}`, "error");
  }
}

async function handleQuickAction(label) {
  const lower = label.toLowerCase();
  if (lower.includes("approve investigation")) return runAnalysis();
  if (lower.includes("return to live")) return goLive();
  if (lower.includes("rewind 60")) {
    state.live = false; state.selectedMs = Math.max(state.startMs, state.selectedMs - 60000); await loadSnapshot();
    return askKarl({action: "reconstruct"}, "Rewind 60 seconds");
  }
  if (lower.includes("explain selected")) {
    if (!state.selectedKey) return addMessage("Select a resource on the map first and I’ll explain its reconstructed state.", "system");
    return askKarl({action: "explain_resource"}, label);
  }
  if (lower.includes("warning")) return askKarl({action: "warnings"}, label);
  if (lower.includes("deep investigation") || lower.includes("prepare")) return askKarl({action: "message", message: "Investigate the root cause"}, label);
  if (lower.includes("reconstruct") || lower.includes("deterministic") || lower.includes("changes in window")) return askKarl({action: "reconstruct"}, label);
  if (lower.includes("inspect first change")) {
    const key = state.diff.changes?.[0]?.resourceKey;
    const object = state.snapshot.find((item) => resourceKey(item) === key);
    if (object) return openResource(object);
    return addMessage("The first changed object no longer exists at this timestamp. Use the timeline to inspect it before deletion.", "system");
  }
  return askKarl({action: "message", message: label}, label);
}

async function goLive() {
  state.live = true; state.selectedMs = state.endMs; elements.slider.value = 1000; await loadSnapshot();
}

function toast(message) {
  elements.toast.textContent = message; elements.toast.classList.add("show");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => elements.toast.classList.remove("show"), 1800);
}

let sliderTimer;
elements.slider.max = "1000";
elements.slider.addEventListener("input", () => {
  const ratio = Number(elements.slider.value) / 1000;
  state.selectedMs = state.startMs + (state.endMs - state.startMs) * ratio;
  state.live = ratio > .999;
  updateTimeChrome();
  clearTimeout(sliderTimer); sliderTimer = setTimeout(loadSnapshot, 180);
});
elements.search.addEventListener("input", () => { state.query = elements.search.value; renderResourceList(); });
elements.refresh.addEventListener("click", async () => { await loadTimeline(); await loadSnapshot(); toast("Cluster memory refreshed"); });
elements.live.addEventListener("click", goLive);
elements.namespace.addEventListener("change", async () => { state.baselineMs = null; await loadTimeline(); await loadDemoWindow(); await loadSnapshot(); });
elements.baseline.addEventListener("click", () => { state.baselineMs = state.selectedMs; toast(`Baseline set at ${fmtTime(state.baselineMs)}`); loadSnapshot(); });
elements.play.addEventListener("click", () => {
  if (state.replayTimer) { clearInterval(state.replayTimer); state.replayTimer = null; elements.play.textContent = "▶ Replay"; return; }
  state.live = false; if (state.selectedMs >= state.endMs - 1000) state.selectedMs = state.startMs;
  elements.play.textContent = "Ⅱ Pause";
  state.replayTimer = setInterval(async () => {
    state.selectedMs = Math.min(state.endMs, state.selectedMs + Math.max((state.endMs - state.startMs) / 40, 1000));
    await loadSnapshot();
    if (state.selectedMs >= state.endMs) { clearInterval(state.replayTimer); state.replayTimer = null; elements.play.textContent = "▶ Replay"; }
  }, 850);
});
elements.chatForm.addEventListener("submit", async (event) => {
  event.preventDefault(); const message = elements.chatInput.value.trim(); if (!message) return;
  elements.chatInput.value = ""; await askKarl({action: "message", message}, message);
});
elements.closeDrawer.addEventListener("click", () => elements.drawer.close());
document.querySelectorAll(".drawer-tabs button").forEach((button) => button.addEventListener("click", () => selectDrawerTab(button.dataset.tab)));
elements.askKarl.addEventListener("click", () => { elements.drawer.close(); askKarl({action: "explain_resource"}, `Explain ${labelFromKey(state.selectedKey)}`); });
elements.copyManifest.addEventListener("click", async () => { if (!state.selectedObject) return; await navigator.clipboard.writeText(toYaml(state.selectedObject)); toast("Manifest copied"); });
window.addEventListener("resize", () => requestAnimationFrame(drawEdges));

async function init() {
  await refreshHealth();
  try {
    await loadTimeline(); await loadDemoWindow(); await loadSnapshot();
    const greeting = await askKarl({action: "greet"});
    if (!greeting) setQuickActions(["Reconstruct this moment", "Show warning events"]);
  } catch (error) {
    addMessage(`Kravel UI is ready, but the local API did not return cluster state: ${error.message}`, "error");
  }
  setInterval(refreshHealth, 10000);
  setInterval(async () => { if (state.live && !state.loading) { await loadTimeline(); await loadSnapshot(); } }, 15000);
}

init();
