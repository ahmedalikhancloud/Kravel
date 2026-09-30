"use strict";
const byId = (id) => document.getElementById(id), ui = Object.fromEntries(["lock", "unlockPanel", "unlockForm", "key", "consolePanel", "history", "preview", "previewNote", "confirm", "cancel", "commandForm", "command", "run", "notice"].map((id) => [id, byId(id)]));
let previewId = "", deadline = 0, commands = [], index = 0, busy = false;
function unlocked(value) { ui.unlockPanel.hidden = value; ui.consolePanel.hidden = !value; ui.lock.hidden = !value; if (value) ui.command.focus(); }
async function request(path, body) {
  const response = await fetch(`/v1/console/${path}`, body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {});
  const result = await response.json(); if (response.status === 401 && path !== "unlock") unlocked(false); if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`); return result;
}
function output(command, value, error = false) { const title = document.createElement("div"), body = document.createElement("pre"); title.className = "command"; title.textContent = `kravel-demo $ ${command}`; body.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2); if (error) body.className = "error"; ui.history.append(title, body); while (ui.history.children.length > 60) ui.history.firstElementChild.remove(); ui.history.scrollTop = ui.history.scrollHeight; }
function discard() { previewId = ""; ui.preview.hidden = true; }
function changed() { if (window.parent !== window) window.parent.postMessage({type: "kravel.operator.completed"}, "http://127.0.0.1:8080"); }
function working(value) { busy = value; ui.run.disabled = value; ui.confirm.disabled = value; ui.command.readOnly = value; }
ui.unlockForm.addEventListener("submit", async (event) => { event.preventDefault(); ui.notice.textContent = ""; try { await request("unlock", {token: ui.key.value.trim()}); ui.key.value = ""; unlocked(true); } catch (error) { ui.key.value = ""; ui.notice.textContent = error.message; } });
ui.lock.addEventListener("click", async () => { try { await request("lock", {}); discard(); unlocked(false); } catch (error) { ui.notice.textContent = error.message; } });
ui.commandForm.addEventListener("submit", async (event) => {
  event.preventDefault(); if (busy) return; const command = ui.command.value.trim(); if (!command) return; discard(); working(true); commands.push(command); if (commands.length > 30) commands.shift(); index = commands.length;
  try { const result = await request("command", {command}); output(command, result.output); ui.notice.textContent = result.note; if (result.previewId) { previewId = result.previewId; deadline = Date.now()+result.expiresInSeconds*1000; ui.preview.hidden = false; } else changed(); ui.command.value = ""; }
  catch (error) { output(command, error.message, true); } finally { working(false); ui.command.focus(); }
});
ui.confirm.addEventListener("click", async () => { if (busy || !previewId) return; working(true); const nonce = previewId; discard(); try { const result = await request("confirm", {previewId: nonce}); output("[explicit manual confirmation]", result.output); ui.notice.textContent = result.note; changed(); } catch (error) { output("[confirmation refused]", error.message, true); } finally { working(false); } });
ui.cancel.addEventListener("click", discard);
ui.command.addEventListener("keydown", (event) => { if (!["ArrowUp", "ArrowDown"].includes(event.key) || busy) return; event.preventDefault(); index = Math.min(commands.length, Math.max(0, index+(event.key === "ArrowUp" ? -1 : 1))); ui.command.value = commands[index] || ""; });
setInterval(() => { if (!previewId) return; const seconds = Math.max(0, Math.ceil((deadline-Date.now())/1000)); ui.previewNote.textContent = `Manual write preview · expires in ${seconds}s. This is your action, not Karl’s.`; if (!seconds) { discard(); ui.notice.textContent = "Preview expired. Run the command again for a fresh dry-run."; } }, 500);
request("session").then((result) => unlocked(result.unlocked)).catch((error) => { ui.notice.textContent = error.message; });
