#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
OBSERVABILITY_STATE="${ROOT_DIR}/.kravel-observability-state"

stop_port_forward() {
  local pid=${1:-}
  local expected=${2:-}
  if [[ "${pid}" =~ ^[0-9]+$ && -r "/proc/${pid}/cmdline" ]]; then
    if tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -Fq -- "${expected}"; then
      kill "${pid}" 2>/dev/null || true
    fi
  fi
}

if [[ -f "${OBSERVABILITY_STATE}" ]]; then
  # shellcheck disable=SC1090
  source "${OBSERVABILITY_STATE}"
  stop_port_forward "${GRAFANA_PORT_FORWARD_PID:-}" "kravel-grafana"
  stop_port_forward "${MLFLOW_PORT_FORWARD_PID:-}" "kravel-mlflow"
fi

kubectl delete namespace kravel-demo kravel-system kravel-observability --ignore-not-found --wait=true
kubectl delete clusterrole kravel-killercoda-observer --ignore-not-found
kubectl delete clusterrolebinding kravel-killercoda-observer --ignore-not-found
rm -f "${ROOT_DIR}/.kravel-demo-state" "${OBSERVABILITY_STATE}"
printf 'Removed the Kravel demo and observability namespaces, RBAC objects, port-forwards, and local timestamp state.\n'
