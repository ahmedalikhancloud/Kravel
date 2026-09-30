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
    if [[ "$attempt" == 4 ]]; then return 1; fi
    printf 'Retrying in %ss: %s\n' "$((attempt * 2))" "$*" >&2
    sleep "$((attempt * 2))"
  done
}

assert_prerequisites() {
  command -v docker >/dev/null || die "docker is required."
  command -v kubectl >/dev/null || die "kubectl is required."
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
    for key in KRAVEL_UI_PID GRAFANA_PID MLFLOW_PID; do
      pid="$(sed -n "s/^${key}=//p" "$KRAVEL_STATE" | tail -n 1)"
      if [[ "$pid" =~ ^[0-9]+$ ]]; then kill "$pid" 2>/dev/null || true; fi
    done
  fi
  # Clean up port forwards created by releases that used a PowerShell JSON state file.
  if [[ -f "$KRAVEL_LEGACY_STATE" ]]; then
    for key in kravelUiPid grafanaPid mlflowPid; do
      pid="$(sed -n "s/.*\"${key}\"[^0-9]*\([0-9][0-9]*\).*/\1/p" "$KRAVEL_LEGACY_STATE" | tail -n 1)"
      if [[ "$pid" =~ ^[0-9]+$ ]]; then kill "$pid" 2>/dev/null || true; fi
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

timestamp() {
  date -u +'%Y-%m-%dT%H:%M:%S.%3NZ'
}

wait_resource() {
  local kind="$1" name="$2" annotation="${3:-}" deadline=$((SECONDS + 90))
  local -a probe=(kubectl -n kravel-system exec deployment/kravel -- python -m kravel.cli probe-resource --kind "$kind" --name "$name" --namespace kravel-demo)
  if [[ -n "$annotation" ]]; then probe+=(--annotation "$annotation"); fi
  while (( SECONDS < deadline )); do
    if "${probe[@]}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  die "Timed out waiting for Kravel to record $kind kravel-demo/$name"
}

payments_endpoints_present() {
  [[ -n "$(kubectl -n kravel-demo get endpoints payments-api -o 'jsonpath={.subsets[*].addresses[*].ip}' 2>/dev/null || true)" ]]
}

payments_endpoints_absent() {
  [[ -z "$(kubectl -n kravel-demo get endpoints payments-api -o 'jsonpath={.subsets[*].addresses[*].ip}' 2>/dev/null || true)" ]]
}

reports_unschedulable() {
  local reason
  reason="$(kubectl -n kravel-demo get pods -l app=reports-worker -o 'jsonpath={.items[*].status.conditions[?(@.type=="PodScheduled")].reason}' 2>/dev/null || true)"
  [[ "$reason" == *Unschedulable* ]]
}

wait_pod_reason() {
  local selector="$1" pattern="$2" deadline=$((SECONDS + ${3:-120})) reasons
  while (( SECONDS < deadline )); do
    reasons="$(kubectl -n kravel-demo get pods -l "$selector" -o 'jsonpath={.items[*].status.containerStatuses[*].state.waiting.reason}' 2>/dev/null || true)"
    [[ "$reasons" =~ $pattern ]] && return 0
    sleep 2
  done
  die "Timed out waiting for Pod reason $pattern"
}

qwen_profile() {
  if [[ "${KRAVEL_QWEN_PROFILE:-fast}" == "thinking" ]]; then
    QWEN_MODEL='ai/qwen3:4b-thinking-2507-q4_K_M'
    QWEN_CONTEXT=8192
    QWEN_REASONING=384
  else
    QWEN_MODEL='ai/qwen3:4b-instruct-2507-q4_K_M'
    QWEN_CONTEXT=4096
    QWEN_REASONING=0
  fi
  export QWEN_MODEL QWEN_CONTEXT QWEN_REASONING
}

configure_kravel_model() {
  kubectl -n kravel-system set env deployment/kravel \
    "KRAVEL_LLM_MODEL=$QWEN_MODEL" \
    "KRAVEL_LLM_REASONING_BUDGET=$QWEN_REASONING" >/dev/null
}
