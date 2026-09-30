#!/usr/bin/env bash
set -Eeuo pipefail
. "$(dirname "$0")/common.sh"

runs=1
while (($#)); do
  case "$1" in
    --runs|-Runs) runs="$2"; shift 2 ;;
    *) die "Unknown option: $1" ;;
  esac
done
[[ "$runs" =~ ^[1-9]$|^10$ ]] || die "--runs must be from 1 through 10"
assert_prerequisites
baseline="$(read_state BASELINE_AT)"
incident="$(read_state INCIDENT_AT)"
scenario="$(read_state SCENARIO)"
for ((run=1; run<=runs; run++)); do
  section "LangGraph incident pipeline run $run of $runs"
  kubectl -n kravel-system exec deployment/kravel -- python -m kravel.cli pipeline \
    --baseline "$baseline" --incident "$incident" --namespace kravel-demo --scenario "$scenario"
done
printf '\nRefresh Grafana at http://localhost:3000 and open MLflow Traces at http://localhost:5000.\n'
