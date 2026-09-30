# Kravel local demo guide

This demo stays on your laptop. Kubernetes runs in Docker Desktop, Qwen runs in Docker Model Runner, and every browser page is exposed through a localhost-only port-forward. No hosted LLM, API key, Codespace, or public URL is needed.

## 1. One-time setup

In Docker Desktop:

1. Enable Kubernetes and wait until it reports **Running**.
2. Enable **Model Runner** and GPU acceleration if your machine supports it.
3. Enable Model Runner's localhost TCP access on port `12434`.
4. Give Docker Desktop at least 8 GB memory if available; Kravel itself is light, while Qwen and the observability containers use most of the allocation.

Install Git for Windows. You can stay in PowerShell or Command Prompt—the `.cmd` launchers find Git Bash automatically and do not require changing PowerShell's script execution policy.

## 2. Prepare everything

From the repository directory:

```bat
demo\local\prepare.cmd
```

This downloads and warms the fast Qwen 4B instruct profile, builds Kravel, removes the old Laya namespace and image, installs the split ServiceAccounts, and starts Prometheus, Grafana, and MLflow. The first run takes the longest because container images and the model must be downloaded.

## 3. Start a clean demo

```bat
demo\local\demo.cmd
```

The command recreates `kravel-demo` in a healthy state and prints these pages:

- Kravel debugger
- Local Slack approval inbox, with its temporary local approval credential
- Grafana
- MLflow

Open Kravel and Local Slack in separate browser tabs. The credential disappears from the Local Slack address bar immediately after the page loads and is never committed to the repository.

## 4. Break exactly one workload

Pick any one:

```bat
demo\local\scenario.cmd break oom
demo\local\scenario.cmd break imagepull
demo\local\scenario.cmd break crashloop
demo\local\scenario.cmd break configmap
```

Or break all four at once:

```bat
demo\local\scenario.cmd break all
```

The scenarios produce:

| Choice | Failure | Safe approved repair |
|---|---|---|
| `oom` | container allocates beyond a 32 MiB limit | raise only `oom-demo` to 128 MiB |
| `imagepull` | nonexistent BusyBox tag | restore `busybox:1.36` |
| `crashloop` | startup exits with code 42 | restore the sleep command |
| `configmap` | `MODE=broken` causes startup failure | restore `MODE=healthy` and restart `config-demo` |

## 5. Show the investigation

1. Refresh Kravel. The affected object glows amber or red in the 3D cluster arcade.
2. Move the pointer over the board to tilt the view. Notice the custom Kravel pointer over the page and the hand pointer over interactive objects.
3. Click a Pod, Deployment, or ConfigMap. Kravel scrolls smoothly to the read-only tool output and shows its live sanitized manifest.
4. Click the incident in the left rail or ask Karl: `Diagnose every current failure. Use evidence and state uncertainty.`
5. Karl calls only read tools and returns a grounded diagnosis. The UI shows Qwen latency, tool count, and trace ID.
6. Choose **Propose fix**. The separate broker resolves the fixed repair, asks Kubernetes to perform a server dry-run, logs the proposal, and starts the five-minute timer.
7. In Local Slack, inspect the exact command and expand **Inspect server dry-run output**. Click **👍 Approve**.
8. The broker performs that one structured repair and records the approver, result, timing, and audit entry. The model does not execute it.
9. Refresh Kravel and watch the object return to green.

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

In MLflow, open the **Kravel Guarded Debugger** experiment, then open an investigation trace. One root agent span contains the input guardrail, every Qwen inference, every Kubernetes tool call, each tool-evidence guardrail, output guardrail, and final issue discovery. Span inputs and outputs are bounded metadata, not raw credentials. The input-guardrail latency metric includes both the question scan and tool-evidence scans.

## 7. Reset and repeat

Restore all four labs while keeping Kravel and the dashboards online:

```bat
demo\local\reset.cmd
```

Restore only one lab:

```bat
demo\local\scenario.cmd reset imagepull
```

Remove the entire disposable installation and stop its port-forwards:

```bat
demo\local\reset.cmd --full
```

The full reset leaves downloaded images and Qwen cached so the next setup is faster.

## Troubleshooting

Check the current state:

```bat
demo\local\scenario.cmd status
kubectl get pods -A
```

If a page is not reachable, run `demo\local\demo.cmd --connect-only`; it replaces the localhost port-forwards without resetting the labs. Running `demo.cmd` without the flag starts a fresh healthy demo. If preparation fails, the script prints Pod details and recent container logs for the failing component.
