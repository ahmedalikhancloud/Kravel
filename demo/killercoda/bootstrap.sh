#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
STATE_FILE="${ROOT_DIR}/.kravel-demo-state"
LOCAL_IMAGE="kravel:killercoda"
REMOTE_IMAGE="${KRAVEL_IMAGE:-}"

say() {
  printf '\n==> %s\n' "$*"
}

fail() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

command -v kubectl >/dev/null 2>&1 || fail "kubectl is required. Open the Killercoda Kubernetes playground, not the plain Ubuntu playground."

say "Waiting for the Killercoda Kubernetes node"
kubectl wait --for=condition=Ready node --all --timeout=120s

say "Resetting only the disposable Kravel demo namespaces"
kubectl delete namespace kravel-demo kravel-system --ignore-not-found --wait=true
kubectl delete clusterrole kravel-killercoda-observer --ignore-not-found
kubectl delete clusterrolebinding kravel-killercoda-observer --ignore-not-found

MANIFEST="${ROOT_DIR}/deploy/killercoda.yaml"
TEMP_MANIFEST=""
IMAGE_ARCHIVE=""
cleanup() {
  [ -z "${TEMP_MANIFEST}" ] || rm -f "${TEMP_MANIFEST}"
  [ -z "${IMAGE_ARCHIVE}" ] || rm -f "${IMAGE_ARCHIVE}"
}
trap cleanup EXIT

if [ -n "${REMOTE_IMAGE}" ]; then
  say "Using published image ${REMOTE_IMAGE}"
  TEMP_MANIFEST=$(mktemp)
  sed \
    -e "s|image: ${LOCAL_IMAGE}|image: ${REMOTE_IMAGE}|" \
    -e 's|imagePullPolicy: Never|imagePullPolicy: IfNotPresent|' \
    "${MANIFEST}" > "${TEMP_MANIFEST}"
  MANIFEST="${TEMP_MANIFEST}"
else
  command -v docker >/dev/null 2>&1 || fail "docker is unavailable. Set KRAVEL_IMAGE to a published image, then run this script again."
  command -v ctr >/dev/null 2>&1 || fail "ctr is unavailable. Set KRAVEL_IMAGE to a published image, then run this script again."

  say "Building Kravel locally from the cloned repository"
  docker build --tag "${LOCAL_IMAGE}" "${ROOT_DIR}"

  say "Importing the image into Kubernetes' containerd image store"
  IMAGE_ARCHIVE=$(mktemp --suffix=.tar)
  docker save --output "${IMAGE_ARCHIVE}" "${LOCAL_IMAGE}"
  if [ "$(id -u)" -eq 0 ]; then
    ctr --namespace k8s.io images import "${IMAGE_ARCHIVE}" >/dev/null
  elif command -v sudo >/dev/null 2>&1; then
    sudo ctr --namespace k8s.io images import "${IMAGE_ARCHIVE}" >/dev/null
  else
    fail "containerd import needs root. Run with sudo or use KRAVEL_IMAGE=ghcr.io/owner/kravel:tag."
  fi
fi

say "Starting the Kravel collector before creating the workload"
kubectl apply -f "${MANIFEST}"
kubectl -n kravel-system rollout status deployment/kravel --timeout=180s

say "Creating the healthy checkout workload"
kubectl apply -f "${ROOT_DIR}/demo/killercoda/lab.yaml"
kubectl -n kravel-demo rollout status deployment/checkout-api --timeout=180s

say "Giving the watch streams time to record the baseline"
sleep 4
BASELINE_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
printf 'BASELINE_AT=%q\n' "${BASELINE_AT}" > "${STATE_FILE}"

STORE_COUNTS=$(kubectl -n kravel-system exec deployment/kravel -- \
  node -e "fetch('http://127.0.0.1:8080/readyz').then(r=>r.text()).then(console.log)" 2>/dev/null)

say "Baseline is ready"
printf 'Baseline timestamp: %s\n' "${BASELINE_AT}"
printf 'Collector state: %s\n' "${STORE_COUNTS}"
kubectl -n kravel-demo get configmap api-config
kubectl -n kravel-demo get deployment checkout-api
kubectl -n kravel-demo get pods

cat <<'EOF'

Next, introduce the failure:
  bash demo/killercoda/break-cluster.sh
EOF
