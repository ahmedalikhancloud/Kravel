#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_FILE="${ROOT_DIR}/.kravel-demo-state"

if [[ ! -f "${STATE_FILE}" ]]; then
  echo "No Kravel demo state found. Run these first:" >&2
  echo "  bash demo/killercoda/bootstrap.sh" >&2
  echo "  bash demo/killercoda/run-multi-incident.sh" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${STATE_FILE}"

if [[ "${SCENARIO_NAME:-}" != "multi-incident" || -z "${BASELINE_AT:-}" || -z "${INCIDENT_AT:-}" ]]; then
  echo "The saved state is not from the multi-incident scenario." >&2
  echo "Run: bash demo/killercoda/run-multi-incident.sh" >&2
  exit 1
fi

echo "==> Reconstructing the complete multi-incident window"
kubectl -n kravel-system exec deploy/kravel -- \
  node src/multi-demo-report.mjs "${BASELINE_AT}" "${INCIDENT_AT}" kravel-demo

cat <<EOF

Raw temporal API equivalents (run through a port-forward if desired):
  GET /v1/state/rewind?timestamp=${BASELINE_AT}&namespace=kravel-demo
  GET /v1/state/diff?from=${BASELINE_AT}&to=${INCIDENT_AT}&namespace=kravel-demo
  GET /v1/context?incidentAt=${INCIDENT_AT}&lookback=10m&namespace=kravel-demo

For the hosted-LLM investigation, set GROQ_API_KEY and run:
  bash demo/killercoda/multi-agent-investigate.sh
EOF
