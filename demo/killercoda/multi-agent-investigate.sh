#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_FILE="${ROOT_DIR}/.kravel-demo-state"

if [[ ! -f "${STATE_FILE}" ]]; then
  echo "Run bootstrap.sh and run-multi-incident.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${STATE_FILE}"
if [[ "${SCENARIO_NAME:-}" != "multi-incident" ]]; then
  echo "Run: bash demo/killercoda/run-multi-incident.sh" >&2
  exit 1
fi

QUESTION="${*:-Investigate the complete incident window. Identify every independent failure. For each one, give the exact first observed state-change time, the first symptom time, the causal resource and field, and the supporting resource keys. Distinguish silent state failures from failures that emitted Kubernetes Warning events. Do not stop after finding the first cause.}"

exec bash "${ROOT_DIR}/demo/killercoda/agent-investigate.sh" "${QUESTION}"
