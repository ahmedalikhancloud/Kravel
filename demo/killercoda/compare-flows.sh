#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
STATE_FILE="${ROOT_DIR}/.kravel-demo-state"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ -f "${STATE_FILE}" ]] || fail "Run bootstrap.sh and run-multi-incident.sh first."
# shellcheck disable=SC1090
source "${STATE_FILE}"
[[ "${SCENARIO_NAME:-}" == "multi-incident" ]] || fail "Run: bash demo/killercoda/run-multi-incident.sh"
kubectl -n kravel-observability get deployment kravel-prometheus >/dev/null 2>&1 \
  || fail "Run: bash demo/killercoda/observability-up.sh"

GROQ_KEY=${GROQ_API_KEY:-}
LAYA_KEY=${KRAVEL_LAYA_API_KEY:-}
LAYA_URL=${KRAVEL_LAYA_URL:-}

if [[ -z "${GROQ_KEY}" ]]; then
  read -rsp 'Groq API key: ' GROQ_KEY
  printf '\n'
fi
if [[ -z "${LAYA_URL}" ]]; then
  read -rsp 'Temporary Laya HTTPS URL (ending in /v1/systemone): ' LAYA_URL
  printf '\n'
fi
if [[ -z "${LAYA_KEY}" ]]; then
  read -rsp 'Temporary Laya bearer token: ' LAYA_KEY
  printf '\n'
fi

[[ -n "${GROQ_KEY}" ]] || fail "A Groq API key is required"
[[ "${LAYA_URL}" == https://* ]] || fail "The remote Laya URL must use HTTPS"

printf 'Runtime credentials and the Laya URL are being streamed over kubectl exec stdin; they are not stored in Kubernetes or MLflow.\n'
printf '%s\n%s\n%s\n' "${GROQ_KEY}" "${LAYA_KEY}" "${LAYA_URL}" | \
  kubectl -n kravel-system exec -i deployment/kravel -- \
    node src/compare-cli.mjs \
      --secrets-stdin \
      --baseline "${BASELINE_AT}" \
      --incident "${INCIDENT_AT}" \
      --namespace kravel-demo \
      --scenario multi-incident

printf '\nRefresh Grafana after a few seconds. Open MLflow for per-comparison child runs.\n'
