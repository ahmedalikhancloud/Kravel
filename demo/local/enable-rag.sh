#!/usr/bin/env bash
set -Eeuo pipefail
. "$(dirname "$0")/common.sh"
assert_prerequisites

check_idle() {
activity="$(curl -fsS --max-time 5 http://127.0.0.1:8080/v1/activity)" || die "Open the existing demo first: bash demo/local/demo.sh --connect-only"
[[ "$activity" != *'true'* ]] || die "Finish active investigations and verification before enabling retrieval."
approval_token="$(kubectl -n kravel-system get secret kravel-approval-token -o jsonpath='{.data.token}' | base64 --decode)"
proposals="$(curl -fsS --max-time 5 -H "X-Kravel-Approval-Token: $approval_token" http://127.0.0.1:8081/v1/proposals)"
unset approval_token
[[ ! "$proposals" =~ \"status\"[[:space:]]*:[[:space:]]*\"(pending|approved|executing)\" ]] || die "Resolve active approval requests before updating."
}
section "Checking active work before updating the debugger"
check_idle

section "Building free CPU-only retrieval (first run downloads public models; no account or key)"
docker build --file "$KRAVEL_ROOT/Dockerfile.retrieval" --tag kravel-retrieval:local "$KRAVEL_ROOT"
docker build --tag kravel:local "$KRAVEL_ROOT"

section "Starting an isolated knowledge service; existing workloads, approvals and history are preserved"
check_idle
kubectl apply -f "$KRAVEL_ROOT/deploy/retrieval-local.yaml"
kubectl -n kravel-observability rollout restart deployment/kravel-knowledge >/dev/null
rollout kravel-observability kravel-knowledge 6m
check_idle
kubectl -n kravel-system create configmap kravel-knowledge-settings \
  --from-literal=KRAVEL_RAG_URL=http://kravel-knowledge.kravel-observability.svc.cluster.local:8084 \
  --from-literal=KRAVEL_SYNTHETIC_RUNBOOKS=1 --dry-run=client -o yaml | kubectl apply -f -
# Add only the optional environment source; don't rotate credentials or reapply
# the entire local stack, which could overwrite operator-selected settings.
kubectl -n kravel-system patch deployment kravel --type=strategic \
  -p '{"spec":{"template":{"spec":{"containers":[{"name":"kravel","envFrom":[{"configMapRef":{"name":"kravel-knowledge-settings","optional":true}}]}]}}}}' >/dev/null
kubectl -n kravel-system set env deployment/kravel KRAVEL_RAG_URL- KRAVEL_SYNTHETIC_RUNBOOKS- >/dev/null
kubectl -n kravel-system rollout restart deployment/kravel >/dev/null
rollout kravel-system kravel 4m

printf '\nHybrid retrieval enabled. Reconnect with: bash demo/local/demo.sh --connect-only\n'
printf 'Open Karl’s knowledge lab, search for a clue, then Run the local retrieval comparison.\n'
printf 'To disable without deleting data: kubectl -n kravel-system set env deployment/kravel KRAVEL_RAG_URL=\n'
