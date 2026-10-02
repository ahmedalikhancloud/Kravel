#!/usr/bin/env bash
set -Eeuo pipefail
. "$(dirname "$0")/common.sh"

assert_prerequisites
qwen_profile

section "Verifying Docker Model Runner"
curl -fsS --max-time 5 http://127.0.0.1:12434/engines/v1/models >/dev/null 2>&1 || die "Docker Desktop's local Model Runner API is unavailable (port 12434). Enable Model Runner/TCP access or restart Docker Desktop. Do not install a second standalone runner on the same port. No cluster resources were changed."
if ! docker model context inspect kravel-desktop >/dev/null 2>&1; then
  docker model context create kravel-desktop --host http://127.0.0.1:12434 --description "Kravel localhost-only Docker Desktop Model Runner"
fi
docker model context use kravel-desktop
docker model status || die "Enable Docker Desktop Model Runner, GPU inference, and localhost TCP port 12434, then retry."

section "Downloading the local Qwen debugger profile: $QWEN_MODEL"
retry docker model pull "$QWEN_MODEL"
docker model configure --context-size "$QWEN_CONTEXT" "$QWEN_MODEL"
docker model run --detach "$QWEN_MODEL"

section "Building the Kravel debugger"
docker build --tag kravel:local "$KRAVEL_ROOT"
docker build --file "$KRAVEL_ROOT/Dockerfile.evaluation" --tag kravel-evaluation:local "$KRAVEL_ROOT"

section "Caching demo and observability images"
for image in busybox:1.36 prom/prometheus:v3.13.3 grafana/grafana:13.1.6 ghcr.io/mlflow/mlflow:v3.14.0; do retry docker pull "$image"; done

section "Removing obsolete Laya resources"
kubectl delete namespace kravel-ai --ignore-not-found --wait=true
docker image rm kravel-laya:local >/dev/null 2>&1 || true

section "Creating the local-only approval credential"
kubectl create namespace kravel-system --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace kravel-demo --dry-run=client -o yaml | kubectl apply -f -
approval_token="${KRAVEL_LOCAL_APPROVAL_TOKEN:-$(openssl rand -hex 24)}"
kubectl -n kravel-system create secret generic kravel-approval-token --from-literal="token=$approval_token" --dry-run=client -o yaml | kubectl apply -f -
operator_token="$(openssl rand -hex 24)"
kubectl -n kravel-system create secret generic kravel-operator-token --from-literal="token=$operator_token" --dry-run=client -o yaml | kubectl apply -f -
unset operator_token
if [[ -n "${SLACK_BOT_TOKEN:-}" && -n "${SLACK_CHANNEL_ID:-}" ]]; then
  kubectl -n kravel-system create secret generic kravel-slack --from-literal="bot-token=$SLACK_BOT_TOKEN" --from-literal="channel-id=$SLACK_CHANNEL_ID" --dry-run=client -o yaml | kubectl apply -f -
  printf 'Real Slack reaction polling enabled. Tokens were stored only in a Kubernetes Secret.\n'
else
  kubectl -n kravel-system delete secret kravel-slack --ignore-not-found >/dev/null
  printf 'Using the self-contained Local Slack approval inbox. No Slack credentials are required.\n'
fi

section "Starting the read-only debugger and isolated approval broker"
kubectl apply -f "$KRAVEL_ROOT/deploy/local.yaml"
configure_kravel_model
kubectl -n kravel-system rollout restart deployment/kravel deployment/kravel-approval-broker deployment/kravel-operator >/dev/null
rollout kravel-system kravel 4m
rollout kravel-system kravel-approval-broker 4m
rollout kravel-system kravel-operator 4m

section "Starting Prometheus, Grafana, and MLflow"
kubectl apply -f "$KRAVEL_ROOT/deploy/observability-local.yaml"
for deployment in kravel-prometheus kravel-grafana kravel-mlflow; do rollout kravel-observability "$deployment" 6m; done

section "Starting the isolated local MLflow judge worker (no Kubernetes identity)"
kubectl apply -f "$KRAVEL_ROOT/deploy/evaluation-local.yaml"
kubectl -n kravel-observability set env deployment/kravel-evaluator "KRAVEL_JUDGE_MODEL=${KRAVEL_JUDGE_MODEL:-$QWEN_MODEL}" >/dev/null
rollout kravel-observability kravel-evaluator 6m

section "Checking local Qwen connectivity from the read-only agent"
kubectl -n kravel-system exec deployment/kravel -- python -m kravel.cli check-llm

printf '\nPreparation complete. Run: bash demo/local/demo.sh\n'
printf 'Fast model: %s\n' "$QWEN_MODEL"
