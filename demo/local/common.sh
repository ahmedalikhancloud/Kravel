#!/usr/bin/env bash
set -Eeuo pipefail

KRAVEL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KRAVEL_STATE="$KRAVEL_ROOT/.kravel-local-state.env"
KRAVEL_LEGACY_STATE="$KRAVEL_ROOT/.kravel-local-state.json"

section() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

retry() {
  local attempt
  for attempt in 1 2 3 4; do
    "$@" && return 0
    [[ "$attempt" == 4 ]] && return 1
    printf 'Retrying in %ss: %s\n' "$((attempt * 2))" "$*" >&2
    sleep "$((attempt * 2))"
  done
}

assert_prerequisites() {
  command -v docker >/dev/null || die "docker is required."
  command -v kubectl >/dev/null || die "kubectl is required."
  command -v openssl >/dev/null || die "openssl is required (included with Git Bash)."
  docker info >/dev/null || die "Docker Desktop is not running."
  local context
  context="$(kubectl config current-context 2>/dev/null || true)"
  [[ "$context" == "docker-desktop" ]] || die "Refusing Kubernetes context '$context'. Run: kubectl config use-context docker-desktop"
  kubectl wait --for=condition=Ready node --all --timeout=180s
}

wait_until() {
  local description="$1" timeout="$2"; shift 2
  local deadline=$((SECONDS + timeout))
  until "$@"; do
    (( SECONDS >= deadline )) && die "Timed out waiting for $description"
    sleep 2
  done
}

deployment_diagnostics() {
  local namespace="$1" deployment="$2"
  printf '\n--- %s diagnostics ---\n' "$deployment" >&2
  kubectl -n "$namespace" get pods -l "app=$deployment" -o wide >&2 || true
  kubectl -n "$namespace" describe pods -l "app=$deployment" >&2 || true
  kubectl -n "$namespace" logs "deployment/$deployment" --all-containers --tail=200 >&2 || true
}

rollout() {
  local namespace="$1" deployment="$2" timeout="${3:-5m}"
  if ! kubectl -n "$namespace" rollout status "deployment/$deployment" "--timeout=$timeout"; then
    deployment_diagnostics "$namespace" "$deployment"
    return 1
  fi
}

read_state() {
  local key="$1"
  [[ -f "$KRAVEL_STATE" ]] || die "No demo state found. Run demo/local/demo.sh first."
  sed -n "s/^${key}=//p" "$KRAVEL_STATE" | tail -n 1
}

stop_port_forwards() {
  local key pid
  if [[ -f "$KRAVEL_STATE" ]]; then
    for key in KRAVEL_UI_PID APPROVAL_PID GRAFANA_PID MLFLOW_PID; do
      pid="$(sed -n "s/^${key}=//p" "$KRAVEL_STATE" | tail -n 1)"
      [[ "$pid" =~ ^[0-9]+$ ]] && kill "$pid" 2>/dev/null || true
    done
  fi
  if [[ -f "$KRAVEL_LEGACY_STATE" ]]; then
    for key in kravelUiPid approvalPid grafanaPid mlflowPid; do
      pid="$(sed -n "s/.*\"${key}\"[^0-9]*\([0-9][0-9]*\).*/\1/p" "$KRAVEL_LEGACY_STATE" | tail -n 1)"
      [[ "$pid" =~ ^[0-9]+$ ]] && kill "$pid" 2>/dev/null || true
    done
  fi
  sleep 1
}

start_port_forward() {
  local namespace="$1" service="$2" local_port="$3" remote_port="$4"
  local log="${TMPDIR:-/tmp}/kravel-${service}.log" pid
  nohup kubectl -n "$namespace" port-forward --address=127.0.0.1 "service/$service" "$local_port:$remote_port" >"$log" 2>&1 &
  pid=$!
  sleep 2
  if ! kill -0 "$pid" 2>/dev/null; then
    printf 'Port forward for %s failed:\n' "$service" >&2
    tail -n 20 "$log" >&2 || true
    return 1
  fi
  printf '%s\n' "$pid"
}

timestamp() { date -u +'%Y-%m-%dT%H:%M:%S.%3NZ'; }

qwen_profile() {
  if [[ "${KRAVEL_QWEN_PROFILE:-fast}" == "thinking" ]]; then
    QWEN_MODEL='ai/qwen3:4b-thinking-2507-q4_K_M'; QWEN_CONTEXT=12288; QWEN_REASONING=384
  else
    QWEN_MODEL='ai/qwen3:4b-instruct-2507-q4_K_M'; QWEN_CONTEXT=12288; QWEN_REASONING=0
  fi
  export QWEN_MODEL QWEN_CONTEXT QWEN_REASONING
}

configure_kravel_model() {
  kubectl -n kravel-system set env deployment/kravel "KRAVEL_LLM_MODEL=$QWEN_MODEL" "KRAVEL_LLM_REASONING_BUDGET=$QWEN_REASONING" >/dev/null
}
