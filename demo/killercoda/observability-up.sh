#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
STATE_FILE="${ROOT_DIR}/.kravel-observability-state"

say() {
  printf '\n==> %s\n' "$*"
}

fail() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

stop_previous() {
  local pid=${1:-}
  local expected=${2:-}
  if [[ "${pid}" =~ ^[0-9]+$ && -r "/proc/${pid}/cmdline" ]]; then
    if tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -Fq -- "${expected}"; then
      kill "${pid}" 2>/dev/null || true
    fi
  fi
}

command -v kubectl >/dev/null 2>&1 || fail "kubectl is required"
kubectl -n kravel-system get deployment kravel >/dev/null 2>&1 || fail "Run bootstrap.sh first so Prometheus has a Kravel target"

if [[ -f "${STATE_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${STATE_FILE}"
  stop_previous "${GRAFANA_PORT_FORWARD_PID:-}" "kravel-grafana"
  stop_previous "${MLFLOW_PORT_FORWARD_PID:-}" "kravel-mlflow"
  sleep 1
fi

say "Deploying resource-capped Prometheus, Grafana, and MLflow"
kubectl apply -f "${ROOT_DIR}/deploy/observability-killercoda.yaml"
for deployment in kravel-prometheus kravel-grafana kravel-mlflow; do
  kubectl -n kravel-observability rollout status "deployment/${deployment}" --timeout=240s
done

say "Opening browser-facing Killercoda ports"
nohup kubectl -n kravel-observability port-forward --address=0.0.0.0 service/kravel-grafana 3000:3000 \
  > /tmp/kravel-grafana-port-forward.log 2>&1 &
GRAFANA_PORT_FORWARD_PID=$!
nohup kubectl -n kravel-observability port-forward --address=0.0.0.0 service/kravel-mlflow 5000:5000 \
  > /tmp/kravel-mlflow-port-forward.log 2>&1 &
MLFLOW_PORT_FORWARD_PID=$!

{
  printf 'GRAFANA_PORT_FORWARD_PID=%q\n' "${GRAFANA_PORT_FORWARD_PID}"
  printf 'MLFLOW_PORT_FORWARD_PID=%q\n' "${MLFLOW_PORT_FORWARD_PID}"
} > "${STATE_FILE}"

for port in 3000 5000; do
  deadline=$((SECONDS + 30))
  until curl --fail --silent --max-time 2 "http://127.0.0.1:${port}/" >/dev/null 2>&1; do
    [[ "${SECONDS}" -lt "${deadline}" ]] || fail "port ${port} did not become reachable; inspect /tmp/kravel-*-port-forward.log"
    sleep 1
  done
done

say "Observability is ready"
if [[ -r /etc/killercoda/host ]]; then
  printf 'Grafana: %s\n' "$(sed 's/PORT/3000/g' /etc/killercoda/host)"
  printf 'MLflow:  %s\n' "$(sed 's/PORT/5000/g' /etc/killercoda/host)"
else
  printf 'Grafana is forwarded on host port 3000; MLflow is forwarded on host port 5000.\n'
fi

cat <<'EOF'

These demo UIs are intentionally anonymous and contain synthetic data only.
Do not place production evidence, API keys, tunnel tokens, or private endpoint URLs in them.
EOF
