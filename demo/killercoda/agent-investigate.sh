#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
STATE_FILE="${ROOT_DIR}/.kravel-demo-state"
API_KEY="${KRAVEL_LLM_API_KEY:-${GROQ_API_KEY:-}}"
MODEL="${KRAVEL_LLM_MODEL:-openai/gpt-oss-20b}"
BASE_URL="${KRAVEL_LLM_BASE_URL:-https://api.groq.com/openai/v1}"
QUESTION="${*:-Reconstruct the incident, identify the leading causal candidate, and explain which evidence supports or weakens that conclusion.}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[ -f "${STATE_FILE}" ] || fail "Run bootstrap.sh and break-cluster.sh first."
# shellcheck disable=SC1090
source "${STATE_FILE}"

[ -n "${INCIDENT_AT:-}" ] || fail "Run break-cluster.sh before investigating."
[ -n "${API_KEY}" ] || fail "Set GROQ_API_KEY (free Groq key) or KRAVEL_LLM_API_KEY, then run this script again."

printf 'Starting the read-only agent with model %s.\n' "${MODEL}"
printf 'The API key is streamed to the process over kubectl exec and is not stored in the Pod or repository.\n\n'

printf '%s' "${API_KEY}" | kubectl -n kravel-system exec -i deployment/kravel -- \
  node src/agent-cli.mjs \
  --api-key-stdin \
  --base-url "${BASE_URL}" \
  --model "${MODEL}" \
  --baseline "${BASELINE_AT}" \
  --incident "${INCIDENT_AT}" \
  --namespace kravel-demo \
  --question "${QUESTION}"
