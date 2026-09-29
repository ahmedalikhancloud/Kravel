# Fully local Kravel demo

This is the supported presentation path. Kubernetes, Laya, Qwen, Kravel, Prometheus, Grafana, and MLflow all run on your laptop. There are no API keys or public URLs.

## What the demo shows

1. Kravel watches a healthy cluster and records a known-good baseline.
2. The script introduces realistic Kubernetes failures.
3. Kravel reconstructs the incident window from historical state and Events.
4. An input guardrail checks the evidence before Laya sees it.
5. Laya classifies the incident cheaply.
6. An output guardrail validates Laya's probabilities.
7. A policy gate chooses a predefined runbook or escalates to Qwen.
8. Qwen receives separately guarded input, calls read-only temporal tools, and produces a guarded investigation.
9. The routine path executes deterministic read-only checks; either path generates a remediation proposal that remains pending human approval.
10. Grafana and MLflow show the latency added by every stage.

## One-time laptop setup

### 1. Update the NVIDIA driver

Docker Model Runner currently requires NVIDIA driver `576.57` or newer on Windows. Install a current driver from NVIDIA, reboot, then verify:

```powershell
nvidia-smi
```

### 2. Install WSL 2

Open PowerShell as Administrator:

```powershell
wsl --install
```

Reboot when Windows asks. Then update WSL:

```powershell
wsl --update
wsl --status
```

### 3. Install and configure Docker Desktop

Install the latest [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/). In Docker Desktop:

1. Use Linux containers.
2. Open **Settings → General** and enable the WSL 2 engine.
3. Leave **Use containerd for pulling and storing images** enabled.
4. Open **Kubernetes** and create a one-node cluster using the `kubeadm` provisioner. The local manifests use `imagePullPolicy: Never`, and this provisioner is the least surprising path for locally built images.
5. Open **Settings → AI** and enable Docker Model Runner.
6. Enable GPU-backed inference.
7. Leave host-side Model Runner TCP access disabled; Kubernetes reaches it through Docker's internal network.

Verify the cluster:

```powershell
kubectl config use-context docker-desktop
kubectl get nodes
docker model status
```

The scripts deliberately refuse to operate against any Kubernetes context other than `docker-desktop`.

### 4. Clone Kravel

```powershell
git clone https://github.com/ahmedalikhancloud/Kravel.git
cd Kravel
```

## Prepare everything before presentation day

Run once while you have a reliable internet connection:

```powershell
.\demo\local\prepare.ps1
```

This command:

- downloads `ai/qwen3:4b-thinking-2507-q4_K_M`;
- limits its context window to 4,096 tokens;
- builds the local Kravel image;
- builds the CPU-only Laya image;
- downloads Laya's English checkpoint into a persistent Kubernetes volume;
- pulls the workload and observability images;
- starts every component once;
- confirms a Kravel Pod can reach Docker Model Runner.

The first run can take several minutes and needs multiple gigabytes of disk space. Do not leave this step until the presentation begins.

## Run the main escalation demo

```powershell
.\demo\local\demo.ps1 -Scenario escalation
```

The script creates four failures:

- a ConfigMap regression followed by `CrashLoopBackOff`;
- Service selector drift that silently removes all backends;
- a rollout referencing a nonexistent image;
- an impossible node selector causing `FailedScheduling`.

Multiple independent failures and severe classes should make the policy gate escalate to Qwen. The terminal prints:

- Laya's classification and policy reasons;
- each stage latency;
- every temporal tool selected by Qwen;
- the guarded Qwen report;
- a dry-run remediation proposal marked `awaiting_human_review` and `remediationExecuted: false`.

The exact route is determined by the real local Laya output. If confidence is unexpectedly low or multiple classes cross the positive threshold, escalation is the intended safe behavior.

## Show the routine path

To demonstrate a smaller, potentially high-confidence incident:

```powershell
.\demo\local\demo.ps1 -Scenario routine
```

This creates only the ConfigMap regression. If Laya meets the configured confidence and margin thresholds, the policy selects `predefined_runbook`; otherwise it safely escalates to Qwen. Neither route mutates the cluster during remediation.

## Open the dashboards

The demo script creates localhost-only port forwards:

- Grafana: [http://localhost:3000](http://localhost:3000)
- MLflow: [http://localhost:5000](http://localhost:5000)

Grafana's **Kravel Local Incident Pipeline** dashboard shows:

- observed end-to-end latency;
- Laya and Qwen inference latency;
- all four guardrail timings;
- evidence, policy, temporal-tool, proposal, and MLflow timing;
- Laya probabilities;
- Qwen tool-call count;
- aggregate p95 stage latency after repeated runs.

MLflow's **Kravel Local Incident Pipeline** experiment contains one parent run per pipeline execution and one child run per measured stage. MLflow stores numeric timing and bounded labels only; it does not store cluster evidence, prompts, reports, endpoints, or credentials.

The `mlflow_logging` measurement covers the MLflow requests completed before that measurement is written. The final metric-write request cannot measure itself, so it is intentionally excluded.

## Build a better latency sample

Reuse the same recorded incident window without breaking the cluster again:

```powershell
.\demo\local\run-pipeline.ps1 -Runs 3
```

This is useful for showing warm-model latency and building Grafana's p95 charts. The first Qwen request after an idle period may be slower because the model is loaded on demand.

## Presentation sequence

A compact narration is:

1. Show the healthy Pods.
2. Run `demo.ps1` and explain each injected failure.
3. Point out Laya's fast classification.
4. Explain the policy decision and why severe or ambiguous evidence escalates.
5. Show Qwen choosing time-travel tools instead of receiving unrestricted cluster access.
6. Show the routine branch's read-only automation evidence, then the proposal's `remediationExecuted: false` and `awaiting_human_review` fields.
7. Open Grafana for the stage-latency view.
8. Open MLflow and expand one run to show the individual guardrail and inference stages.

## Cleanup

Remove disposable workloads, Kravel history, dashboards, and port forwards while retaining downloaded models:

```powershell
.\demo\local\reset.ps1
```

To also remove Laya and its persistent checkpoint cache:

```powershell
.\demo\local\reset.ps1 -Full
```

The Qwen model and Docker images remain cached. Remove those separately through Docker Desktop only if you intentionally want to reclaim disk space.

## Troubleshooting

### `docker model` is not recognized

Update Docker Desktop and enable Docker Model Runner under **Settings → AI**.

### GPU-backed inference is unavailable

Verify the NVIDIA driver, run `wsl --update`, restart Docker Desktop, and confirm GPU-backed inference is enabled. Your local Qwen run may otherwise be slow or unavailable.

### `ErrImageNeverPull` for `kravel:local` or `kravel-laya:local`

Confirm Docker Desktop Kubernetes uses the `kubeadm` provisioner, the containerd image store is enabled, and `prepare.ps1` completed successfully. Then rebuild:

```powershell
.\demo\local\prepare.ps1
```

### Laya startup takes several minutes

The first startup downloads and loads its checkpoint. Inspect it with:

```powershell
kubectl -n kravel-ai logs deployment/kravel-laya
kubectl -n kravel-ai get pods,pvc
```

Later starts reuse `laya-model-cache` unless you run `reset.ps1 -Full` or reset Docker Desktop's Kubernetes cluster.

### Kravel cannot reach Qwen

```powershell
kubectl -n kravel-system exec deployment/kravel -- node -e "fetch('http://model-runner.docker.internal/engines/v1/models').then(r=>console.log(r.status)).catch(console.error)"
```

If it fails, verify Docker Model Runner is enabled and restart Docker Desktop. Do not expose the Model Runner API to your LAN as a workaround.

### The dashboard is empty

Run the pipeline at least once, wait a few seconds for Prometheus, then refresh Grafana:

```powershell
.\demo\local\run-pipeline.ps1
```

### Port 3000 or 5000 is already used

Stop the conflicting local service or edit the local ports in `demo/local/demo.ps1`. The port forwards intentionally bind only to `127.0.0.1`.

## Safety boundaries

- Run only the included synthetic scenarios.
- The Kubernetes role is read-only.
- Model and dashboard endpoints are not published externally.
- Docker Model Runner's API has no authentication, so external TCP access stays disabled.
- Guardrails are deterministic filters and validators, not proof that model output is safe or correct.
- No remediation command is executed; a human remains the approval boundary.
