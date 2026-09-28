# Groq versus Laya comparison demo

This optional demo compares Kravel's existing Groq tool-calling investigator with a Laya System-1 classifier over the same incident window. Prometheus and Grafana show aggregate timing signals; MLflow groups each pair as one comparison with two child runs.

Laya is deliberately outside Killercoda. Loading its model alongside Kubernetes, Prometheus, Grafana, and MLflow risks exhausting a small playground. The launcher uses one English checkpoint in a GitHub Codespace and gives the playground a temporary, authenticated HTTPS endpoint.

## What is and is not comparable

The two flows have different capabilities:

- `groq_agent` lets `openai/gpt-oss-20b` choose temporal tools and produce a narrative investigation.
- `laya_classifier` deterministically builds a compact evidence package and asks four independent yes/no questions in one System-1 request.

The dashboard therefore reports both end-to-end time and component time. End-to-end time is the user-visible comparison; model time is useful for performance analysis but does not make the capabilities equivalent.

The bundled Laya checkpoint is used zero-shot. Treat its probabilities as a reproducible demo baseline, not authoritative root-cause classification; validate the exact questions against labelled incidents and fine-tune or calibrate before production use.

## 1. Start the Killercoda side

Use a fresh Kubernetes playground checkout:

```bash
git pull --ff-only
bash demo/killercoda/bootstrap.sh
bash demo/killercoda/run-multi-incident.sh
bash demo/killercoda/observability-up.sh
```

The last command prints temporary browser links for Grafana and MLflow. Killercoda traffic links are anonymous and effectively public to anyone who has the unguessable URL. Use only the synthetic demo.

## 2. Start the free-allowance Laya side

On the [Kravel repository](https://github.com/ahmedalikhancloud/Kravel), select **Code → Codespaces → Create codespace on main**. A personal GitHub account includes a monthly Codespaces quota; it is limited rather than an unlimited hosting plan. In the Codespace terminal, run:

```bash
bash demo/codespaces/laya-server.sh
```

The first execution installs pinned Laya `0.3.20`, downloads the English checkpoint, and can take several minutes. The script generates a bearer token at runtime and attempts to make port `8000` public. If it cannot change the visibility automatically, open the Codespace **PORTS** tab, right-click port `8000`, and set **Port Visibility → Public**. It then prints the temporary HTTPS URL and token.

Keep the Codespace running during the comparison. Do not save the terminal output, paste either value into chat, commit it, add it to a Kubernetes Secret, or put it in Grafana or MLflow. A public Codespaces port is reachable by anyone who knows its URL, but inference still requires the random bearer token. Use only synthetic demonstration data.

GitHub documents both the [personal-account included quota](https://docs.github.com/en/codespaces/troubleshooting/troubleshooting-included-usage) and [public port behavior](https://docs.github.com/en/codespaces/reference/security-in-github-codespaces). When the demo is finished, run `bash demo/codespaces/laya-server.sh stop`, then stop or delete the Codespace to conserve included usage.

## 3. Run the measured comparison

```bash
bash demo/killercoda/compare-flows.sh
```

Enter the Groq key, temporary Laya URL, and temporary Laya token when prompted. The script streams all three values into the already-running Kravel Pod over standard input. They are never added to command-line arguments, Pod environment variables, manifests, the SQLite benchmark table, Prometheus labels, or MLflow.

Run the comparison several times to build a useful time series:

```bash
for run in 1 2 3; do
  bash demo/killercoda/compare-flows.sh
done
```

The script asks for credentials on each run unless they are present in local shell variables. For convenience within a disposable session you may export them, but remember to clear them immediately afterward:

```bash
unset GROQ_API_KEY KRAVEL_LAYA_URL KRAVEL_LAYA_API_KEY
```

## Dashboard interpretation

Grafana shows:

- latest end-to-end latency for each flow;
- Groq temporal tool-call count;
- latency by evidence, model, tools, and total phase;
- Laya's four diagnosis probabilities;
- aggregate p95 over successful runs retained in the short-lived demo database.

MLflow stores only bounded numeric metrics and non-sensitive labels: comparison id, scenario, flow, provider, model, status, error code, timings, tool-call count, aggregate confidence, and diagnosis probabilities. It does not store evidence, prompts, answers, cluster object names, credentials, or endpoint URLs.

Cold Laya startup is not measured because the health-checked server is started before the comparison. The Laya `model` phase is client-observed HTTPS round-trip time, so it includes network latency. Groq `model` time is the sum of its hosted completion calls, while `tools` is local temporal-query execution.

## Failure isolation

If the Codespace stops or its forwarded port becomes private, the Laya child run is marked `unavailable`; Groq, Kravel, Prometheus, Grafana, and MLflow continue. If MLflow is unavailable, comparison records still go to Kravel's SQLite store and Prometheus. The observability deployment uses short retention, resource limits, one replica per component, and `emptyDir` storage.
