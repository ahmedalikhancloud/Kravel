#!/usr/bin/env bash
# Preserve labs, evidence, approval/demo credentials, and all observability data.
set -Eeuo pipefail
. "$(dirname "$0")/common.sh"
assert_prerequisites
check_active_work() {
  local activity proposals
  activity="$(curl -fsS --max-time 5 http://127.0.0.1:8080/v1/activity)" || die "Connect the existing local app first: bash demo/local/demo.sh --connect-only"
  proposals="$(curl -fsS --max-time 5 http://127.0.0.1:8080/v1/proposals)" || die "Approval broker unavailable; refusing to interrupt unknown active work."
  if printf '%s\n' "$activity" | grep -Eq '"(investigationActive|verificationActive)"[[:space:]]*:[[:space:]]*true' ||
     printf '%s\n' "$proposals" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"(pending|approved|executing)"'; then
    die "Wait for current investigations, approvals and recovery checks before upgrading."
  fi
}
section "Checking active work before upgrading the debugger"
check_active_work
section "Configuring the installed thinking planner with a bounded reasoning budget"
qwen_profile
docker model configure --context-size "$QWEN_CONTEXT" "${KRAVEL_THINKING_MODEL:-ai/qwen3:4b-thinking-2507-q4_K_M}" -- --reasoning-budget "${KRAVEL_THINKING_BUDGET:-1024}"
section "Building the extended guarded debugger (no new model downloads)"
docker build --tag kravel:local "$KRAVEL_ROOT"
section "Updating Kravel and the human-approved cluster-admin executor (worker remains read-only)"
check_active_work
kubectl apply -f "$KRAVEL_ROOT/deploy/local.yaml"
kubectl -n kravel-system rollout restart deployment/kravel deployment/kravel-approval-broker
rollout kravel-system kravel 4m
rollout kravel-system kravel-approval-broker 4m
printf '\nUpgrade complete. General cluster operations now require exact-plan human approval. Existing credentials, labs and history were preserved.\n'
printf 'Reconnect without resetting your labs: bash demo/local/demo.sh --connect-only\n'
