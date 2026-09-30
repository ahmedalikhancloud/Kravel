# Fully local Kravel demo

This path keeps Kubernetes, Laya, Qwen, Kravel, Prometheus, Grafana, and MLflow on your laptop. There are no API keys or public URLs.

## One-time setup

1. Install current NVIDIA drivers and reboot.
2. Enable virtualization in your firmware.
3. Install/update WSL 2.
4. Install Docker Desktop and enable Linux containers, the WSL 2 engine, Kubernetes with the `kubeadm` provisioner, Docker Model Runner, GPU-backed inference, and localhost Model Runner TCP access on port `12434`.
5. Give Docker Desktop at least 8 GB of memory. Laya can approach 4 GB while loading.
6. Install [Git for Windows](https://git-scm.com/download/win), which includes Git Bash.

Verify from Git Bash:

```bash
docker info
kubectl config use-context docker-desktop
kubectl get nodes
docker model status
```

The scripts refuse to modify any Kubernetes context except `docker-desktop`.

## Prepare before presentation day

In Git Bash, from the repository root:

```bash
bash demo/local/prepare.sh
```

This downloads the fast `ai/qwen3:4b-instruct-2507-q4_K_M` profile, builds Python Kravel and Laya, caches all container images, loads Laya's English checkpoint, starts the observability stack, and checks that a Kravel Pod can reach Qwen.

The first run can take several minutes and multiple gigabytes. Rerunning it is safe and reuses cached layers/models.

## Main demo

```bash
bash demo/local/demo.sh --scenario escalation
```

The scenario creates four common production failures:

1. a ConfigMap regression followed by `CrashLoopBackOff`;
2. Service selector drift that silently removes all endpoints;
3. a rollout using a nonexistent image;
4. an impossible node selector causing `FailedScheduling`.

The terminal first prints the deterministic time-travel diff and ordered timeline. It then runs the guarded LangGraph pipeline and prints Laya probabilities, the policy route, every stage latency, Qwen's evidence-grounded report when escalated, and the human-review proposal.

It also records the exact baseline and incident timestamps in the disposable demo namespace so the visual cockpit opens directly on the captured failure window.

For a smaller incident:

```bash
bash demo/local/demo.sh --scenario routine
```

If Laya clears the confidence and margin thresholds, this uses the predefined read-only runbook. Low confidence still escalates safely.

## Visual cockpit and dashboards

The demo creates localhost-only port forwards:

- Kravel cockpit: [http://localhost:8080](http://localhost:8080)
- Grafana: [http://localhost:3000](http://localhost:3000)
- MLflow: [http://localhost:5000](http://localhost:5000)

Start with the Kravel cockpit. The center canvas reconstructs the Kubernetes topology at the selected timestamp. Broken resources are red, objects changed from the chosen baseline are amber, and relationship lines show ownership, selectors, ConfigMap reads, service accounts, and storage. Click a resource to inspect its historical YAML, relations, and changed paths.

Drag the bottom timeline to rewind the cluster or use **Replay** to move through the captured sequence. Karl stays synchronized with the selected timestamp and resource. He can summarize deterministic evidence immediately, explain a resource, show warning Events, or ask permission before starting the guarded Laya → policy → Qwen investigation.

In MLflow, choose the **Kravel Local Incident Traces** experiment and open **Traces**. A pipeline execution is one trace; expanding it shows the parent/child waterfall for reconstruction, input/output guardrails, Laya, routing, temporal tools, Qwen, and human review.

Grafana shows current and aggregate stage latency. Run the same captured window several times for a warm-model sample:

```bash
bash demo/local/run-pipeline.sh --runs 3
```

## Why Qwen is faster now

The default uses Qwen3 4B **Instruct**, a 4,096-token context, no hidden-reasoning budget, bounded 3.5 KB tool evidence, and a 520-token ceiling for a requested 180-word report. LangGraph deterministically gathers the two safest useful temporal tools first, so Qwen needs one synthesis request instead of a planning request plus a report request.

To deliberately compare against the slower Thinking profile:

```bash
KRAVEL_QWEN_PROFILE=thinking bash demo/local/prepare.sh
KRAVEL_QWEN_PROFILE=thinking bash demo/local/demo.sh --scenario escalation
```

Return to the fast profile by rerunning both commands without that environment variable.

## Using the `.cmd` launchers

From PowerShell or Command Prompt you may run:

```text
demo\local\prepare.cmd
demo\local\demo.cmd --scenario escalation
demo\local\run-pipeline.cmd --runs 3
demo\local\reset.cmd
```

They locate Git Bash and invoke the same `.sh` scripts. They do not execute PowerShell scripts, so PowerShell's script execution policy is irrelevant.

## Presentation sequence

1. Run the escalation demo and open the Kravel cockpit at `http://localhost:8080`.
2. Click a red or amber resource and show its reconstructed YAML manifest.
3. Drag backward across the event markers to show the healthy earlier state, then press **Replay**.
4. Ask Karl what changed and show his deterministic evidence response.
5. Choose **Prepare deep investigation**, explain the read-only approval card, and approve it.
6. Show incident shards: native Kubernetes signals handle mechanically obvious failures while Laya scores ambiguous correlations.
7. Show `awaiting_human_review` and `remediationExecuted: false`.
8. Open Grafana for stage timings and MLflow **Traces** for the span waterfall.

## Cleanup

Keep downloaded models and images:

```bash
bash demo/local/reset.sh
```

Also delete Laya and its cached checkpoint:

```bash
bash demo/local/reset.sh --full
```

## Troubleshooting

### `docker`, `kubectl`, or `git` is not recognized

Open **Git Bash**, not a stale PowerShell window. If the `.cmd` wrapper cannot find Git Bash, reinstall Git for Windows with its default components.

### Docker Model Runner says port 12434 is already in use

Docker Desktop's runner may already be healthy. Test it in Git Bash:

```bash
curl -fsS http://127.0.0.1:12434/engines/v1/models
docker model context create kravel-desktop --host http://127.0.0.1:12434 --description "Kravel localhost-only Docker Desktop Model Runner"
docker model context use kravel-desktop
docker model status
```

Skip the `create` command if the context already exists. Do not expose this unauthenticated endpoint to your LAN.

### Laya is `OOMKilled`

Confirm Docker Desktop has at least 8 GB available, then restart it and rerun preparation:

```bash
kubectl -n kravel-ai describe pods -l app=kravel-laya
kubectl -n kravel-ai logs deployment/kravel-laya --previous
bash demo/local/prepare.sh
```

### Kravel cannot reach Qwen

```bash
kubectl -n kravel-system exec deployment/kravel -- python -m kravel.cli check-llm
```

Confirm Model Runner and host-side TCP support are enabled, then restart Docker Desktop.

### Grafana or MLflow is not ready

```bash
kubectl -n kravel-observability get pods
kubectl -n kravel-observability logs deployment/kravel-grafana
kubectl -n kravel-observability logs deployment/kravel-mlflow
```

### Dashboard is empty

Run at least one pipeline, wait a few seconds for Prometheus, and refresh:

```bash
bash demo/local/run-pipeline.sh
```

### Port 8080, 3000, or 5000 is occupied

Stop the conflicting local service, run `bash demo/local/reset.sh`, and retry. Port forwards bind only to `127.0.0.1`.

## Safety boundaries

- Run only the included synthetic namespaces.
- Kravel's Kubernetes role is read-only.
- Model and dashboard endpoints are not published externally.
- Raw evidence, prompts, reports, URLs, and secrets are excluded from MLflow trace payloads and Prometheus labels.
- Guardrails are deterministic filters/validators, not proof of model safety.
- No remediation command is executed; a human remains the approval boundary.
