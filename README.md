# Kravel

Kravel is a local, guarded Kubernetes debugger built for a live demo. Karl—the pixel-art copilot—can inspect Pods, controllers, ConfigMaps, Events, and bounded logs through a strictly read-only ServiceAccount. A separate approval broker can repair four disposable demo failures, but only after Kubernetes server-side dry-run succeeds and a human approves within five minutes.

The main browser UI renders the live namespace as an interactive CSS-3D cluster arcade. Click a Pod, Deployment, ConfigMap, or Service to load its sanitized manifest through the same audited read-only tool API used by the agent.

## What is included

- LangGraph + local Qwen debugging agent with no mutation, shell, exec, proxy, or Secret tools.
- Read-only Kubernetes tools shaped like `get`, `describe`, `events`, and `logs`.
- A separate least-privilege approval broker restricted to four named Deployments and one named ConfigMap in `kravel-demo`.
- Local Slack-style approval inbox by default; real Slack reactions are optional.
- Five-minute approval expiry, structured server dry-run, fixed repair catalog, and an append-only SQLite audit trail.
- Prometheus, a provisioned Grafana dashboard, and persistent MLflow traces with nested spans for guardrails, Qwen calls, tools, and tracing overhead.
- Independent OOMKilled, ImagePullBackOff, CrashLoopBackOff, and bad ConfigMap labs.
- No Groq, Codespaces, hosted model, public callback URL, or API key required.

## Fast local start on Windows

Prerequisites: Docker Desktop with Kubernetes and Model Runner enabled, Git for Windows, and the Docker Desktop Kubernetes context selected.

From Command Prompt or PowerShell:

```bat
demo\local\prepare.cmd
demo\local\demo.cmd
```

`prepare.cmd` downloads the local Qwen profile, builds Kravel, removes obsolete Laya resources, and starts the observability stack. `demo.cmd` creates four healthy labs and prints four localhost URLs.

Open the Kravel URL, then break one lab:

```bat
demo\local\scenario.cmd break imagepull
```

Refresh Kravel, click the red or amber 3D object, ask Karl to diagnose it, prepare the suggested fix, and approve it in the printed Local Slack URL.

Reset every lab without stopping the dashboards:

```bat
demo\local\reset.cmd
```

For the full walk-through, see [DEMO.md](DEMO.md).

## Safety boundary

```text
Browser / Karl
      │
      ▼
Read-only debugger ServiceAccount ── get/list/watch/logs only
      │ diagnosis + allowlisted fix ID
      ▼
Approval broker ServiceAccount ── server dry-run ── 5-minute human approval
      │
      └── patch only: oom-demo, image-demo, crash-demo, config-demo
```

The Qwen process never receives the broker approval token. The broker does not accept arbitrary commands or arbitrary patches: it resolves a fixed fix ID to code-owned structured Kubernetes API operations. Secrets are not a supported read resource.

## Optional real Slack

The self-contained Local Slack page is the easiest demo path. To also post to a real Slack channel, create a bot with `chat:write` and `reactions:read`, invite it to the channel, then set these only in your local shell before running preparation:

```bash
export SLACK_BOT_TOKEN='xoxb-...'
export SLACK_CHANNEL_ID='C...'
bash demo/local/prepare.sh
```

The preparation script writes them to an uncommitted Kubernetes Secret. Kravel never returns or logs the token. If those environment variables are absent, preparation deletes any old Slack Secret and uses only the localhost workflow.

## Local URLs

| Page | Default URL | Purpose |
|---|---|---|
| Kravel | `http://127.0.0.1:8080` | 3D cluster map, Karl, tools, proposals, audit |
| Local Slack | printed by `demo.cmd` | approve or reject a dry-run proposal |
| Grafana | `http://127.0.0.1:3000` | latency, audit, approval, and fix metrics |
| MLflow | `http://127.0.0.1:5000` | nested investigation traces |

All port-forwards bind only to `127.0.0.1`.

## Developer checks

```bash
python -m pytest
bash -n demo/local/*.sh
node --check kravel/web/app.js
node --check kravel/web/slack.js
```

See [architecture](docs/architecture.md), [security](docs/security.md), and [audit trail](docs/audit-trail.md) for the design details.

Apache-2.0 licensed.
