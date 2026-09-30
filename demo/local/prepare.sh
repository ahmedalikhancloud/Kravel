#!/usr/bin/env bash
set -Eeuo pipefail
. "$(dirname "$0")/common.sh"

assert_prerequisites
qwen_profile

section "Verifying Docker Model Runner"
if curl -fsS --max-time 5 http://127.0.0.1:12434/engines/v1/models >/dev/null 2>&1; then
  if ! docker model context inspect kravel-desktop >/dev/null 2>&1; then
    docker model context create kravel-desktop --host http://127.0.0.1:12434 --description "Kravel localhost-only Docker Desktop Model Runner"
  fi
  docker model context use kravel-desktop
fi
docker model status || die "Enable Docker Desktop Model Runner, GPU inference, and localhost TCP port 12434, then retry."

section "Downloading the local Qwen profile: $QWEN_MODEL"
retry docker model pull "$QWEN_MODEL"
docker model configure --context-size "$QWEN_CONTEXT" "$QWEN_MODEL"
docker model run --detach "$QWEN_MODEL"

section "Building Python Kravel and CPU-only Laya"
docker build --tag kravel:local "$KRAVEL_ROOT"
docker build --tag kravel-laya:local --file "$KRAVEL_ROOT/demo/local/laya.Dockerfile" "$KRAVEL_ROOT"

section "Caching workload and observability images"
for image in busybox:1.36 prom/prometheus:v3.13.3 grafana/grafana:13.1.6 ghcr.io/mlflow/mlflow:v3.14.0; do retry docker pull "$image"; done

section "Starting Laya and its persistent model cache"
kubectl apply -f "$KRAVEL_ROOT/deploy/laya-local.yaml"
kubectl -n kravel-ai rollout restart deployment/kravel-laya
rollout kravel-ai kravel-laya 12m

section "Starting Kravel and the observability stack"
kubectl apply -f "$KRAVEL_ROOT/deploy/local.yaml"
configure_kravel_model
kubectl apply -f "$KRAVEL_ROOT/deploy/observability-local.yaml"
rollout kravel-system kravel 4m
for deployment in kravel-prometheus kravel-grafana kravel-mlflow; do rollout kravel-observability "$deployment" 6m; done

section "Checking in-cluster access to Docker Model Runner"
kubectl -n kravel-system exec deployment/kravel -- python -m kravel.cli check-llm

printf '\nPreparation complete. Run: bash demo/local/demo.sh --scenario escalation\n'
printf 'Fast model: %s\n' "$QWEN_MODEL"
printf 'For the slower reasoning comparison: KRAVEL_QWEN_PROFILE=thinking bash demo/local/prepare.sh\n'
