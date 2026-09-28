#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
STATE_FILE="${ROOT_DIR}/.kravel-demo-state"

fail() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

[ -f "${STATE_FILE}" ] || fail "Run: bash demo/killercoda/bootstrap.sh"
# shellcheck disable=SC1090
source "${STATE_FILE}"
kubectl -n kravel-system get deployment kravel >/dev/null 2>&1 || fail "Kravel is not running; rerun bootstrap.sh"

printf '\n==> Introducing a realistic configuration regression\n'
BREAK_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
kubectl -n kravel-demo patch configmap api-config --type merge --patch \
  '{"data":{"STARTUP_MODE":"broken","DATABASE_TIMEOUT_MS":"3","CHANGE_TICKET":"INC-4242"}}'

printf '\n==> Restarting the Deployment so the new ConfigMap values are consumed\n'
kubectl -n kravel-demo rollout restart deployment/checkout-api

printf '\n==> Waiting for the replacement Pod to fail and restart\n'
DEADLINE=$((SECONDS + 90))
RESTARTS=0
WAITING_REASON=""
while [ "${SECONDS}" -lt "${DEADLINE}" ]; do
  RESTARTS=$(kubectl -n kravel-demo get pods -l app=checkout-api \
    -o jsonpath='{range .items[*].status.containerStatuses[*]}{.restartCount}{"\n"}{end}' 2>/dev/null \
    | sort -nr | head -n1)
  RESTARTS=${RESTARTS:-0}
  WAITING_REASON=$(kubectl -n kravel-demo get pods -l app=checkout-api \
    -o jsonpath='{range .items[*].status.containerStatuses[*]}{.state.waiting.reason}{"\n"}{end}' 2>/dev/null \
    | grep -E 'CrashLoopBackOff|Error' | head -n1 || true)
  if [ "${RESTARTS}" -gt 0 ] || [ -n "${WAITING_REASON}" ]; then
    break
  fi
  sleep 2
done

if [ "${RESTARTS}" -eq 0 ] && [ -z "${WAITING_REASON}" ]; then
  fail "The workload did not enter a failing state within 90 seconds. Inspect: kubectl -n kravel-demo get pods"
fi

sleep 4
INCIDENT_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
{
  printf 'BASELINE_AT=%q\n' "${BASELINE_AT}"
  printf 'BREAK_AT=%q\n' "${BREAK_AT}"
  printf 'INCIDENT_AT=%q\n' "${INCIDENT_AT}"
} > "${STATE_FILE}"

printf '\nThe cluster is now broken. Current evidence:\n\n'
kubectl -n kravel-demo get configmap api-config -o jsonpath='ConfigMap mode={.data.STARTUP_MODE}, timeout={.data.DATABASE_TIMEOUT_MS}ms{"\n"}'
kubectl -n kravel-demo get pods -l app=checkout-api
FAILED_POD=$(kubectl -n kravel-demo get pods -l app=checkout-api --sort-by=.metadata.creationTimestamp -o name | tail -n1)
if [ -n "${FAILED_POD}" ]; then
  printf '\nLatest failed-container log:\n'
  kubectl -n kravel-demo logs "${FAILED_POD}" --previous 2>/dev/null || kubectl -n kravel-demo logs "${FAILED_POD}" 2>/dev/null || true
fi

cat <<'EOF'

Now ask Kravel to reconstruct the incident:
  bash demo/killercoda/investigate.sh
EOF
