"use strict";
const byId = (id) => document.getElementById(id);
const state = {unlocked: false, busy: false, preview: null, expires: 0};
const lessons = [
  {id: "oom", icon: "◈", title: "Memory limit", failure: "OOMKilled", text: "A container asks for more memory than its limit allows."},
  {id: "imagepull", icon: "⬡", title: "Missing image", failure: "ImagePullBackOff", text: "An image tag is wrong, so the application cannot start."},
  {id: "crashloop", icon: "↻", title: "Startup error", failure: "CrashLoopBackOff", text: "The application exits, and Kubernetes keeps trying again."},
  {id: "configmap", icon: "▤", title: "Wrong setting", failure: "Bad ConfigMap", text: "A broken setting causes the application to fail at startup."},
  {id: "network", icon: "◇", title: "Disconnected Service", failure: "Selector mismatch", text: "The network address no longer points to its application's Pods."},
];
function node(tag, text = "", className = "") { const item = document.createElement(tag); item.textContent = text; item.className = className; return item; }
function announce(text, error = false) { byId("status").textContent = text; byId("status").classList.toggle("error", error); }
function notify(type, extra = {}) { if (window.parent !== window) window.parent.postMessage({type, ...extra}, "http://127.0.0.1:8080"); }
async function request(path, body) {
  const response = await fetch(`/v1/console/${path}`, {method: body ? "POST" : "GET", headers: {"Content-Type": "application/json"}, ...(body ? {body: JSON.stringify(body)} : {})});
  const data = await response.json();
  if (!response.ok) { if (response.status === 401) { state.unlocked = false; render(); } throw new Error(data.error || `Demo control error ${response.status}`); }
  return data;
}
function render() {
  byId("unlockSection").hidden = state.unlocked; byId("lock").hidden = !state.unlocked;
  document.querySelectorAll(".card button").forEach((button) => { button.disabled = !state.unlocked || state.busy; });
  byId("reset").disabled = !state.unlocked || state.busy; byId("confirm").disabled = state.busy || !state.preview || Date.now() >= state.expires;
  byId("lock").disabled = state.busy; byId("cancel").disabled = state.busy;
}
async function unlock(key) {
  try { await request("unlock", {token: key}); state.unlocked = true; byId("key").value = ""; announce("Unlocked. Choose one practice problem. You'll review its change before applying it."); }
  catch (error) { announce(error.message, true); }
  finally { byId("key").value = ""; render(); }
}
byId("cards").replaceChildren(...lessons.map((lesson) => {
  const card = node("article", "", "card"), button = node("button", "Try this problem ↗");
  button.setAttribute("aria-label", `Try ${lesson.failure}`); button.disabled = true;
  button.addEventListener("click", () => preview(lesson.id, "break"));
  card.append(node("span", lesson.icon, "icon"), node("h3", lesson.title), node("small", lesson.failure), node("p", lesson.text), button); return card;
}));
byId("unlockForm").addEventListener("submit", (event) => { event.preventDefault(); unlock(byId("key").value.trim()); });
byId("lock").addEventListener("click", async () => {
  if (state.busy) return;
  state.busy = true; render();
  try { await request("lock", {}); state.unlocked = false; cancelPreview(); announce("Controls locked. Karl remains read-only."); }
  catch (error) { announce(error.message, true); }
  finally { state.busy = false; render(); }
});
function cancelPreview() { state.preview = null; byId("preview").hidden = true; render(); }
byId("cancel").addEventListener("click", cancelPreview);
byId("reset").addEventListener("click", () => preview("all", "reset"));
async function preview(scenario, action) {
  if (!state.unlocked || state.busy) return;
  state.busy = true; cancelPreview(); announce("Checking activity and asking Kubernetes for a server dry-run…"); render();
  try {
    const data = await request("labs/preview", {scenario, action});
    state.preview = data; state.expires = Date.now()+data.expiresInSeconds*1000;
    byId("previewTitle").textContent = action === "reset" ? "Restore all five practice labs?" : `Create ${lessons.find((l) => l.id === scenario).failure}?`;
    byId("previewNote").textContent = `This will ${action === "reset" ? "restore healthy settings" : "intentionally cause a real failure"} in kravel-demo only. Server dry-run passed. No change has been applied yet.`;
    byId("operations").replaceChildren(...data.dryRuns.map((op) => node("li", op.resource)));
    byId("dryRun").textContent = JSON.stringify(data.dryRuns, null, 2);
    byId("confirm").textContent = action === "reset" ? "Restore healthy labs" : "Break this lab";
    byId("preview").hidden = false; byId("preview").focus(); byId("preview").scrollIntoView({behavior: "smooth", block: "nearest"});
    announce("Preview ready. Review it, then confirm within 60 seconds.");
  } catch (error) { announce(error.message, true); }
  finally { state.busy = false; render(); }
}
byId("confirm").addEventListener("click", async () => {
  if (!state.preview || state.busy || Date.now() >= state.expires) return;
  const reviewed = state.preview; state.busy = true; render(); announce("Applying your reviewed lab changes…");
  try {
    const result = await request("labs/confirm", {previewId: reviewed.previewId}); cancelPreview();
    announce(result.status === "accepted" ? `✓ ${result.accepted.length} patch${result.accepted.length === 1 ? "" : "es"} accepted. ${result.action === "reset" ? "Watch the live view for Pods to become ready." : "Watch the live view for the actual failure; it can take a few seconds."}` : result.error, result.status !== "accepted");
    notify("kravel-lab-applied", {action: result.action, scenario: result.scenario, accepted: result.status === "accepted"});
  } catch (error) { cancelPreview(); announce(error.message, true); }
  finally { state.busy = false; render(); }
});
setInterval(() => { if (state.preview) { const seconds = Math.max(0, Math.ceil((state.expires-Date.now())/1000)); byId("deadline").textContent = seconds ? `Preview expires in ${seconds}s. No automatic execution.` : "Preview expired. Cancel and review a new preview."; render(); } }, 1000);
function publishHeight() { notify("kravel-lab-height", {height: Math.ceil(document.body.getBoundingClientRect().height)+12}); }
new ResizeObserver(publishHeight).observe(document.body);
window.addEventListener("message", (event) => {
  if (event.origin === "http://127.0.0.1:8080" && event.source === window.parent && event.data?.type === "kravel-lab-parent-ready") publishHeight();
});
(async () => {
  const key = new URLSearchParams(location.hash.slice(1)).get("token");
  if (location.hash) history.replaceState(null, "", location.pathname); // never retain the capability in URL/history
  if (key) return unlock(key);
  try { state.unlocked = (await request("session")).unlocked; announce(state.unlocked ? "Unlocked. Choose one problem and review its change." : "Controls locked. Use your private launcher link once to enable these buttons."); }
  catch { announce("Demo controller is not connected. Run the local demo launcher again.", true); }
  render();
})();
