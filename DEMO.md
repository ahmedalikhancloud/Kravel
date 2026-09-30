# Kravel local demo guide

This demo stays on your laptop. Kubernetes runs in Docker Desktop, Qwen runs in Docker Model Runner, and every browser page is exposed through a localhost-only port-forward. No hosted LLM, API key, Codespace, or public URL is needed.

## 1. One-time setup

In Docker Desktop:

1. Enable Kubernetes and wait until it reports **Running**.
2. Enable **Model Runner** and GPU acceleration if your machine supports it.
3. Enable Model Runner's localhost TCP access on port `12434`.
4. Give Docker Desktop at least 8 GB memory if available; Kravel itself is light, while Qwen and the observability containers use most of the allocation.

Install Git for Windows, which includes Git Bash. Open the Kravel folder in File Explorer, right-click, and choose **Open Git Bash here** (under **Show more options** if needed).

Run every terminal command below in that Git Bash window, from the repository directory. All examples use Bash scripts and forward-slash paths. The scripts resolve their repository paths themselves. No PowerShell policy change or Linux-side installation of Docker is required.

## 2. Prepare everything

From the repository directory:

```bash
bash demo/local/prepare.sh
```

This downloads and warms the fast Qwen 4B instruct profile, builds Kravel, removes the old Laya namespace and image, installs separate debugger/broker/human-console ServiceAccounts, and starts Prometheus, Grafana, and MLflow. The first run takes the longest because container images and the model must be downloaded. To upgrade, run preparation again, then reconnect without resetting the labs. The new HTTP lab is installed by starting a clean demo or resetting all labs.

## 3. Start a clean demo

```bash
bash demo/local/demo.sh
```

The command recreates `kravel-demo` in a healthy state and prints these pages:

- Kravel debugger
- Local Slack approval inbox, with its temporary local approval credential
- Grafana
- MLflow

Open Kravel and Local Slack in separate browser tabs. The credential disappears from the Local Slack address bar immediately after the page loads and is never committed to the repository.

### Unlock the embedded human console

Scroll to **Human console** in Kravel. In Git Bash:

```bash
bash demo/local/console-key.sh
```

On Windows this copies a separate unlock key to the clipboard without printing it. Paste it into the console's key field and click **Unlock console**. The key is not a model key, is not mounted into Karl's Pod, and is not put in a URL. The human session lasts 15 minutes. Use **Lock** when done.

This is a **kubectl-compatible API console, not a full Bash/PTY terminal**. It connects to the real cluster using its own restricted ServiceAccount. It supports bounded `get`, `describe`, `logs`, `events`, rollout status, and reviewed edits to named labs. Reads currently render JSON, including with `-o wide`. No arbitrary scripts, pipes, plugins, exec, Secrets, other namespaces, context overrides, or host files. Continue using external Git Bash for preparation, scenario scripts, and reset.

Type into the website console:

```bash
kubectl get pods
kubectl get deployments
kubectl get events
```

To break the image lab from the website:

```bash
kubectl set image deployment/image-demo image-demo=busybox:kravel-demo-image-does-not-exist
```

Inspect the displayed server dry-run and click **Confirm manual change** within 60 seconds. This is explicitly your operator action, not Karl's. The live topology refreshes afterward. A changed resource, expired preview, or reused confirmation is refused.

## 4. Break exactly one workload

Pick any one:

```bash
bash demo/local/scenario.sh break oom
bash demo/local/scenario.sh break imagepull
bash demo/local/scenario.sh break crashloop
bash demo/local/scenario.sh break configmap
bash demo/local/scenario.sh break network
```

Or break all five at once:

```bash
bash demo/local/scenario.sh break all
```

The scenarios produce:

| Choice | Failure | Safe approved repair |
|---|---|---|
| `oom` | container allocates beyond a 32 MiB limit | raise only `oom-demo` to 128 MiB |
| `imagepull` | nonexistent BusyBox tag | restore `busybox:1.36` |
| `crashloop` | startup exits with code 42 | restore the sleep command |
| `configmap` | `MODE=broken` causes startup failure | restore `MODE=healthy` and restart `config-demo` |
| `network` | `demo-gateway` selects no Pods | restore selector `app=net-demo` for a real HTTP workload |

## 5. Show the investigation

1. Refresh Kravel. The affected object's health ring and label turn amber or red in the 3D cluster observatory.
2. Drag the scene to orbit, **Shift-drag** or select **Pan** to move sideways, and scroll over the scene to zoom. Use **Fit cluster** to reset the camera or expand the explorer for more room. With the canvas focused, arrow keys pan, **R** fits, and **F** focuses the selection.
3. Follow the directional links: mint means controller ownership (Deployment → ReplicaSet → Pod), amber means ConfigMap references, and purple means Service selectors. These are observed Kubernetes relationships, not measured traffic. Click a model or its label to open the right-side Inspector; double-click to fly closer. Search and type filters narrow the view without resetting your camera.
4. In the Inspector, switch between **Manifest**, **Describe**, **Events**, and **Logs**, or follow a connection to another resource. Logs belong only to the selected Pod or Pods connected to that resource. Current logs are the default; enable **Previous logs** for a restarted container. On a small screen the sidebar slides in and can be closed with **×**. The expandable keyboard resource directory offers an alternative to clicking models. Click an incident in the Incident desk, or open **Karl** and ask: `Diagnose every current failure. Use evidence and state uncertainty.`
5. Click **Investigate selected** in the Inspector, or **Investigate failures** in the cockpit. Follow actual evidence collection, Qwen reasoning, and guardrails. Findings show supporting **E1…** IDs, evidence strength (not a guessed probability), uncertainty, and prevention. Evidence IDs open bounded observations; resource links focus current 3D objects. Older observations are clearly labeled. Run history survives refresh/restart. Missing reads appear as coverage gaps; collected evidence remains available if Qwen fails. Clear supported cases use one synthesis call; ambiguous cases allow at most one follow-up round. Only one model investigation runs at once.
6. Choose **Propose fix**. The separate broker resolves the fixed repair, asks Kubernetes to perform a server dry-run, logs the proposal, and starts the five-minute timer.
7. In Local Slack, inspect the exact command and expand **Inspect server dry-run output**. Click **👍 Approve**.
8. Watch **Change requests**: dry-run → approval → revalidation → each applied operation. ConfigMap data and Deployment restart are separate operations. The model does not execute them. **Patch accepted** is not the same as fixed.
9. A separate read-only observer checks the applied generation, current owned Pods, and readiness three times over at least six seconds. Only then is **Observed recovery** shown. Verification times out after 90 seconds without rollback or retry. The networking lab observes Ready endpoints; it does not claim a DNS/TCP traffic test. Watch selector links disappear when broken and return after repair.

If nobody approves in five minutes, the proposal expires and nothing changes.
If you reset a lab or edit its configuration while approval is pending, the broker refuses the stale proposal. Prepare a fresh dry-run and approval. A broker restart resumes pending timers but never replays an interrupted execution.

## 6. Show observability

In Grafana, open **Kravel Guarded Debugger**. Useful panels include:

- end-to-end and Qwen latency;
- input and output guardrail latency;
- read-only tool calls;
- MLflow setup, span overhead, and trace flush latency;
- approval states and pending age;
- approved fix count and audited activity.
- mean actual evidence/guardrail/repair/approval/verification stage timings;
- recovery outcomes and separately audited manual operator actions.

In MLflow, open the **Kravel Guarded Debugger** experiment, then an investigation trace. The agent root contains input guardrail, initial evidence reads, collected-evidence guardrail, Qwen inference, focused follow-up tools, output guardrail, and final issue discovery. Repair review, application (with individual operation spans), and recovery observation have separate traces linked by proposal ID. Span inputs/outputs contain metadata, not raw prompts/logs or credentials. Input-guardrail timing includes question and evidence scans. Actual stage timings appear in the cockpit and Grafana.

## 7. Reset and repeat

Restore all five labs while keeping Kravel and the dashboards online:

```bash
bash demo/local/reset.sh
```

Restore only one lab:

```bash
bash demo/local/scenario.sh reset imagepull
```

Remove the entire disposable installation and stop its port-forwards:

```bash
bash demo/local/reset.sh --full
```

The full reset leaves downloaded images and Qwen cached so the next setup is faster.

## Troubleshooting

Check the current state:

```bash
bash demo/local/scenario.sh status
kubectl get pods -A
```

If a page is not reachable, reconnect the localhost port-forwards without resetting the labs:

```bash
bash demo/local/demo.sh --connect-only
```

Running `bash demo/local/demo.sh` without the flag starts a fresh healthy demo. If preparation fails, the script prints Pod details and recent container logs for the failing component.

The 3D explorer requires browser WebGL support. If it is unavailable, Kravel clearly labels a resource-list fallback; inspection and approval safety remain unchanged. Live refreshes preserve your camera and selection. An offline banner means the displayed topology is the last known state, not a current health report.
