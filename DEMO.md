# Kravel demo runbook

This runbook demonstrates Kravel reconstructing four Kubernetes failures, then compares two AI analysis flows in Grafana and MLflow:

- **Groq agent:** chooses Kravel's temporal tools and writes an investigation report.
- **Laya classifier:** evaluates the same incident window and returns four diagnosis probabilities.

Allow about **15–25 minutes** for a first run. You will use two browser workspaces, plus the Grafana and MLflow result tabs:

| Workspace | Purpose |
|---|---|
| Killercoda | Kubernetes, Kravel, Prometheus, Grafana, and MLflow |
| GitHub Codespaces | Temporary Laya System-1 server |

## Before you start

You need:

1. A GitHub account with Codespaces included usage remaining.
2. A Groq API key from the [Groq API Keys page](https://console.groq.com/keys).
3. A fresh [Killercoda Kubernetes playground](https://killercoda.com/playgrounds/scenario/kubernetes).

Keep the Groq key, temporary Laya URL, and temporary Laya token private. Do not paste them into chat, screenshots, commits, Grafana, MLflow, or Kubernetes manifests. The scripts request them with hidden terminal prompts and do not persist them.

## Step 1: Start Kravel in Killercoda

In the Killercoda terminal, clone the repository:

```bash
git clone https://github.com/ahmedalikhancloud/Kravel.git
cd Kravel
```

Build and deploy Kravel, then create the healthy baseline:

```bash
bash demo/killercoda/bootstrap.sh
```

This can take a few minutes while Docker downloads the Node base image. Continue only after you see:

```text
==> Baseline is ready
```

You should also see one healthy `checkout-api` Pod and a collector-state summary.

## Step 2: Break the cluster in four different ways

Run the multi-incident scenario:

```bash
bash demo/killercoda/run-multi-incident.sh
```

The script introduces these failures one at a time:

| # | Change | Result |
|---:|---|---|
| 1 | Regresses `ConfigMap/api-config` | `checkout-api` crash loop |
| 2 | Changes the `payments-api` Service selector | Ready endpoints fall to zero |
| 3 | Deploys a nonexistent `inventory-api` image | `ImagePullBackOff` |
| 4 | Adds an impossible `reports-worker` node selector | `FailedScheduling` |

Continue after you see:

```text
==> All four failures are active
```

The command also prints the baseline, change, symptom, and final incident timestamps. Kravel saves these automatically for later commands.

## Step 3: Prove reconstruction works without an LLM

Run the deterministic report first:

```bash
bash demo/killercoda/multi-investigate.sh
```

Verify that it identifies all four independent failures. This is useful during a presentation because it proves state reconstruction is a Kravel capability rather than an LLM guess.

## Step 4: Start the dashboards

Still in Killercoda, deploy Prometheus, Grafana, and MLflow:

```bash
bash demo/killercoda/observability-up.sh
```

Wait for all three deployments to finish. The script then prints two links:

```text
Grafana: https://...
MLflow:  https://...
```

Open both links in new browser tabs. The pages may be empty until the first comparison run.

These demo UIs are anonymous and intended only for the synthetic scenario. Anyone with a link may be able to open it while the Killercoda session is active.

## Step 5: Start Laya in GitHub Codespaces

Open the [Kravel GitHub repository](https://github.com/ahmedalikhancloud/Kravel), then select:

**Code → Codespaces → Create codespace on main**

In the Codespace terminal, run:

```bash
bash demo/codespaces/laya-server.sh
```

The first run installs Laya and downloads its English checkpoint, so it may take several minutes. Continue after the terminal prints:

```text
Laya is ready. Enter these values only when compare-flows.sh prompts for them:
URL:   https://.../v1/systemone
Token: ...
```

Keep this tab and terminal running. You will need the URL and token in the next step.

If the script says it could not change port visibility automatically:

1. Open the **PORTS** tab at the bottom of the Codespace.
2. Find port `8000`.
3. Right-click it and select **Port Visibility → Public**.

The port is public, but Laya inference still requires the random bearer token. Use it only with this synthetic demo.

## Step 6: Run Groq and Laya over the same incident

Return to the Killercoda terminal and run:

```bash
bash demo/killercoda/compare-flows.sh
```

The script asks for three values. Paste each value and press Enter. The terminal intentionally does not display what you paste.

1. **Groq API key** — from the Groq console.
2. **Temporary Laya HTTPS URL** — the Codespace URL ending in `/v1/systemone`.
3. **Temporary Laya bearer token** — printed beside the URL.

A successful run prints:

- Groq's total time, hosted-model time, temporal-tool count, and investigation report;
- Laya's total time, evidence-building time, model round-trip time, and four probabilities;
- confirmation that comparison metadata reached MLflow.

The two flows are intentionally different. Groq performs a multi-step investigation and generates a narrative. Laya makes four structured, zero-shot decisions in one request. Compare their timings, but do not treat the results as equivalent model-quality measurements.

## Step 7: View the results

Wait a few seconds, then refresh Grafana. The **Kravel Flow Comparison** dashboard shows:

- latest end-to-end latency for both flows;
- evidence, model, tool, and total timing components;
- Groq temporal-tool calls;
- Laya probabilities for all four incident classes;
- aggregate p95 latency over successful runs.

In MLflow, open the **Kravel Incident Flow Comparison** experiment. Each comparison has one parent run and two child runs:

- `groq_agent`
- `laya_classifier`

MLflow receives numeric timings and bounded labels only. It does not receive cluster evidence, prompts, model answers, credentials, or endpoint URLs.

For a better latency chart, run the comparison two or three more times:

```bash
bash demo/killercoda/compare-flows.sh
```

Re-enter the three hidden values each time, then refresh Grafana.

## Suggested presentation flow

Use this short narration:

1. **Show the broken cluster:** display the crash loop, missing Service endpoints, image-pull failure, and unschedulable Pod.
2. **Show the deterministic report:** explain that Kravel recorded the healthy baseline before anything broke.
3. **Point out the silent failure:** selector drift may have no Warning Event, but the state diff still exposes it.
4. **Run the AI comparison:** Groq chooses temporal tools while Laya classifies a compact evidence shard.
5. **Open Grafana:** compare total and component latency rather than presenting only one headline number.
6. **Open MLflow:** show that repeated experiments are grouped and reviewable.
7. **State the limitation:** correlation is evidence, not proof; application logs, audit records, and production metrics would strengthen causal claims.

## Troubleshooting

### Kravel reports `ErrImageNeverPull`

Make sure the checkout contains the node-pinning fix, then rebuild:

```bash
git pull --ff-only
bash demo/killercoda/bootstrap.sh
```

### A Killercoda command times out

Inspect the relevant Pods and recent events:

```bash
kubectl get pods -A
kubectl get events -A --sort-by=.metadata.creationTimestamp | tail -n 40
```

For Kravel specifically:

```bash
kubectl -n kravel-system describe pod -l app.kubernetes.io/name=kravel
kubectl -n kravel-system logs deployment/kravel --tail=100
```

### Grafana or MLflow does not open

Check the observability Pods and port-forward logs:

```bash
kubectl -n kravel-observability get pods
cat /tmp/kravel-grafana-port-forward.log
cat /tmp/kravel-mlflow-port-forward.log
```

Then rerun:

```bash
bash demo/killercoda/observability-up.sh
```

### Grafana has no data

Run `compare-flows.sh`, wait at least five seconds for Prometheus to scrape Kravel, and refresh the dashboard.

### Laya is unavailable

Check all four items:

1. The Codespace is still running.
2. Port `8000` is **Public** in the Codespace **PORTS** tab.
3. The URL starts with `https://` and ends with `/v1/systemone`.
4. You entered the token printed by the current Laya process.

To inspect Laya startup failures inside the Codespace:

```bash
tail -n 100 /tmp/kravel-laya.log
```

You can restart it with:

```bash
bash demo/codespaces/laya-server.sh stop
bash demo/codespaces/laya-server.sh
```

### Groq returns an authentication or rate-limit error

Create or verify the key at the [Groq API Keys page](https://console.groq.com/keys). Free-plan rate limits can change; check the [Groq rate-limit documentation](https://console.groq.com/docs/rate-limits).

## Cleanup

In Killercoda:

```bash
bash demo/killercoda/reset.sh
```

In the Codespace:

```bash
bash demo/codespaces/laya-server.sh stop
```

Finally, stop or delete the Codespace so it does not continue consuming included usage. Killercoda is ephemeral, and Kravel's demo data uses `emptyDir`, so the captured history disappears when the environment is removed.

## What success looks like

At the end of the demo, you should have shown:

- four ordered Kubernetes failures reconstructed from a previously healthy baseline;
- a state-only failure that Kubernetes Warning Events alone would miss;
- an evidence-grounded Groq investigation using read-only temporal tools;
- a Laya probability vector over the same incident window;
- side-by-side latency in Grafana;
- paired, repeatable experiment records in MLflow;
- no API keys, tokens, evidence payloads, or temporary endpoint URLs persisted by the comparison workflow.
