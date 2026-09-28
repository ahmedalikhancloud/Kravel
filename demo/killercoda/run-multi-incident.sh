#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
STATE_FILE="${ROOT_DIR}/.kravel-demo-state"
NAMESPACE="kravel-demo"

say() {
  printf '\n==> %s\n' "$*"
}

fail() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

wait_for_reason() {
  local selector=$1
  local expression=$2
  local timeout=${3:-90}
  local deadline=$((SECONDS + timeout))
  local reasons=""
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    reasons=$(kubectl -n "${NAMESPACE}" get pods -l "${selector}" \
      -o jsonpath='{range .items[*].status.containerStatuses[*]}{.state.waiting.reason}{"\n"}{end}' 2>/dev/null || true)
    if printf '%s\n' "${reasons}" | grep -Eq "${expression}"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

[ -f "${STATE_FILE}" ] || fail "Run: bash demo/killercoda/bootstrap.sh"
kubectl -n kravel-system get deployment kravel >/dev/null 2>&1 || fail "Kravel is not running; rerun bootstrap.sh"

CURRENT_MODE=$(kubectl -n "${NAMESPACE}" get configmap api-config -o jsonpath='{.data.STARTUP_MODE}' 2>/dev/null || true)
[ "${CURRENT_MODE}" = "healthy" ] || fail "The checkout workload is not healthy. Rerun bootstrap.sh before this scenario."

say "Creating the additional healthy production-style workloads"
kubectl apply -f "${ROOT_DIR}/demo/killercoda/multi-lab.yaml"
for deployment in payments-api inventory-api reports-worker; do
  kubectl -n "${NAMESPACE}" rollout status "deployment/${deployment}" --timeout=180s
done

say "Waiting for the healthy payments Service to receive endpoints"
ENDPOINT_DEADLINE=$((SECONDS + 60))
PAYMENT_ENDPOINTS=""
while [ "${SECONDS}" -lt "${ENDPOINT_DEADLINE}" ]; do
  PAYMENT_ENDPOINTS=$(kubectl -n "${NAMESPACE}" get endpoints payments-api \
    -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)
  [ -n "${PAYMENT_ENDPOINTS}" ] && break
  sleep 2
done
[ -n "${PAYMENT_ENDPOINTS}" ] || fail "payments-api never received a healthy endpoint"

sleep 5
BASELINE_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
say "Healthy multi-service baseline recorded at ${BASELINE_AT}"

say "Incident 1/4: ConfigMap regression followed by checkout crash loop"
CONFIG_CHANGE_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
CONFIG_PATCH=$(printf '{"metadata":{"annotations":{"kravel.dev/change-at":"%s","kravel.dev/incident":"config-regression"}},"data":{"STARTUP_MODE":"broken","DATABASE_TIMEOUT_MS":"3","CHANGE_TICKET":"INC-4242"}}' "${CONFIG_CHANGE_AT}")
kubectl -n "${NAMESPACE}" patch configmap api-config --type merge --patch "${CONFIG_PATCH}"
kubectl -n "${NAMESPACE}" rollout restart deployment/checkout-api
wait_for_reason "app=checkout-api" 'CrashLoopBackOff|Error' 90 \
  || fail "checkout-api did not enter a crash loop"
sleep 5
CONFIG_SYMPTOM_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)

say "Incident 2/4: Service selector drift silently removes every payments endpoint"
SERVICE_CHANGE_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
SERVICE_PATCH=$(printf '{"metadata":{"annotations":{"kravel.dev/change-at":"%s","kravel.dev/incident":"service-selector-drift"}},"spec":{"selector":{"app":"payments-api-v2"}}}' "${SERVICE_CHANGE_AT}")
kubectl -n "${NAMESPACE}" patch service payments-api --type merge --patch "${SERVICE_PATCH}"
ENDPOINT_DEADLINE=$((SECONDS + 60))
while [ "${SECONDS}" -lt "${ENDPOINT_DEADLINE}" ]; do
  PAYMENT_ENDPOINTS=$(kubectl -n "${NAMESPACE}" get endpoints payments-api \
    -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)
  [ -z "${PAYMENT_ENDPOINTS}" ] && break
  sleep 2
done
[ -z "${PAYMENT_ENDPOINTS}" ] || fail "payments-api still has endpoints after selector drift"
sleep 5
SERVICE_SYMPTOM_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)

say "Incident 3/4: Inventory rolls out a nonexistent image tag"
IMAGE_CHANGE_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
IMAGE_PATCH=$(printf '{"spec":{"template":{"metadata":{"annotations":{"kravel.dev/change-at":"%s","kravel.dev/incident":"bad-image-rollout"}},"spec":{"containers":[{"name":"inventory-api","image":"busybox:kravel-demo-image-does-not-exist"}]}}}}' "${IMAGE_CHANGE_AT}")
kubectl -n "${NAMESPACE}" patch deployment inventory-api --type strategic --patch "${IMAGE_PATCH}"
wait_for_reason "app=inventory-api" 'ErrImagePull|ImagePullBackOff' 120 \
  || fail "inventory-api did not report an image pull failure"
sleep 5
IMAGE_SYMPTOM_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)

say "Incident 4/4: Reports rollout gets an impossible node selector"
SCHEDULING_CHANGE_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
SCHEDULING_PATCH=$(printf '{"spec":{"template":{"metadata":{"annotations":{"kravel.dev/change-at":"%s","kravel.dev/incident":"impossible-node-selector"}},"spec":{"nodeSelector":{"kravel.dev/nonexistent-node":"true"}}}}}' "${SCHEDULING_CHANGE_AT}")
kubectl -n "${NAMESPACE}" patch deployment reports-worker --type strategic --patch "${SCHEDULING_PATCH}"
SCHEDULING_DEADLINE=$((SECONDS + 90))
UNSCHEDULABLE=""
while [ "${SECONDS}" -lt "${SCHEDULING_DEADLINE}" ]; do
  UNSCHEDULABLE=$(kubectl -n "${NAMESPACE}" get pods -l app=reports-worker \
    -o jsonpath='{range .items[*]}{range .status.conditions[?(@.type=="PodScheduled")]}{.reason}{"\n"}{end}{end}' 2>/dev/null \
    | grep 'Unschedulable' | head -n1 || true)
  [ -n "${UNSCHEDULABLE}" ] && break
  sleep 2
done
[ -n "${UNSCHEDULABLE}" ] || fail "reports-worker did not become unschedulable"
sleep 6
SCHEDULING_SYMPTOM_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
INCIDENT_AT=${SCHEDULING_SYMPTOM_AT}

{
  printf 'SCENARIO_NAME=%q\n' "multi-incident"
  printf 'BASELINE_AT=%q\n' "${BASELINE_AT}"
  printf 'CONFIG_CHANGE_AT=%q\n' "${CONFIG_CHANGE_AT}"
  printf 'CONFIG_SYMPTOM_AT=%q\n' "${CONFIG_SYMPTOM_AT}"
  printf 'SERVICE_CHANGE_AT=%q\n' "${SERVICE_CHANGE_AT}"
  printf 'SERVICE_SYMPTOM_AT=%q\n' "${SERVICE_SYMPTOM_AT}"
  printf 'IMAGE_CHANGE_AT=%q\n' "${IMAGE_CHANGE_AT}"
  printf 'IMAGE_SYMPTOM_AT=%q\n' "${IMAGE_SYMPTOM_AT}"
  printf 'SCHEDULING_CHANGE_AT=%q\n' "${SCHEDULING_CHANGE_AT}"
  printf 'SCHEDULING_SYMPTOM_AT=%q\n' "${SCHEDULING_SYMPTOM_AT}"
  printf 'INCIDENT_AT=%q\n' "${INCIDENT_AT}"
} > "${STATE_FILE}"

say "All four failures are active"
printf 'Baseline:       %s\n' "${BASELINE_AT}"
printf 'Config change:  %s\n' "${CONFIG_CHANGE_AT}"
printf 'Service drift:  %s\n' "${SERVICE_CHANGE_AT}"
printf 'Bad image:      %s\n' "${IMAGE_CHANGE_AT}"
printf 'Bad scheduling: %s\n' "${SCHEDULING_CHANGE_AT}"
printf 'Final incident: %s\n\n' "${INCIDENT_AT}"
kubectl -n "${NAMESPACE}" get deployments,pods,services,endpoints
printf '\nRecent warnings:\n'
kubectl -n "${NAMESPACE}" get events --field-selector type=Warning --sort-by=.metadata.creationTimestamp | tail -n 20 || true

cat <<'EOF'

Reconstruct every incident:
  bash demo/killercoda/multi-investigate.sh

Run the hosted LLM over the same timeline:
  bash demo/killercoda/multi-agent-investigate.sh
EOF
