#!/usr/bin/env bash
set -Eeuo pipefail
. "$(dirname "$0")/common.sh"
assert_prerequisites
qwen_profile
kubectl -n kravel-observability get deployment kravel-mlflow >/dev/null || die "Run preparation first; this upgrade preserves an existing demo."
section "Building debugger and isolated evaluator (no labs, tokens, or history reset)"
docker build --tag kravel:local "$KRAVEL_ROOT"
docker build --file "$KRAVEL_ROOT/Dockerfile.evaluation" --tag kravel-evaluation:local "$KRAVEL_ROOT"
section "Enabling MLflow's HTTP artifact transport (PVC and historical experiments preserved)"
kubectl apply -f "$KRAVEL_ROOT/deploy/observability-local.yaml"
rollout kravel-observability kravel-mlflow 6m
kubectl apply -f "$KRAVEL_ROOT/deploy/local.yaml"
configure_kravel_model
kubectl -n kravel-system rollout restart deployment/kravel >/dev/null
rollout kravel-system kravel 4m
kubectl apply -f "$KRAVEL_ROOT/deploy/evaluation-local.yaml"
kubectl -n kravel-observability set env deployment/kravel-evaluator "KRAVEL_JUDGE_MODEL=${KRAVEL_JUDGE_MODEL:-$QWEN_MODEL}" >/dev/null
kubectl -n kravel-observability rollout restart deployment/kravel-evaluator >/dev/null
rollout kravel-observability kravel-evaluator 6m
printf '\nUpgrade complete. Reconnect: bash demo/local/demo.sh --connect-only\n'
