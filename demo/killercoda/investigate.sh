#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
STATE_FILE="${ROOT_DIR}/.kravel-demo-state"

if [ ! -f "${STATE_FILE}" ]; then
  printf 'Run bootstrap.sh and break-cluster.sh first.\n' >&2
  exit 1
fi
# shellcheck disable=SC1090
source "${STATE_FILE}"

if [ -z "${INCIDENT_AT:-}" ]; then
  printf 'Run break-cluster.sh before investigating.\n' >&2
  exit 1
fi

kubectl -n kravel-system exec deployment/kravel -- \
  node src/demo-report.mjs "${BASELINE_AT}" "${INCIDENT_AT}" kravel-demo

cat <<EOF

Raw API equivalents (run through a port-forward if desired):
  GET /v1/state/rewind?timestamp=${BASELINE_AT}&namespace=kravel-demo
  GET /v1/state/diff?from=${BASELINE_AT}&to=${INCIDENT_AT}&namespace=kravel-demo
  GET /v1/context?incidentAt=${INCIDENT_AT}&lookback=5m&namespace=kravel-demo

For the real hosted-LLM tool-calling investigation:
  export GROQ_API_KEY='your-key'
  bash demo/killercoda/agent-investigate.sh
EOF
