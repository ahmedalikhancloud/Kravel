#!/usr/bin/env bash
set -Eeuo pipefail
. "$(dirname "$0")/common.sh"

full=false
while (($#)); do
  case "$1" in
    --full|-Full) full=true; shift ;;
    *) die "Unknown option: $1" ;;
  esac
done
assert_prerequisites

if [[ "$full" == false ]]; then
  section "Restoring all five demo workloads to their healthy baseline"
  bash "$KRAVEL_ROOT/demo/local/scenario.sh" reset all
  fresh_demo_view
  printf '\nHealthy reset complete. Kravel, Grafana, MLflow, and Local Slack stayed online.\n'
  exit 0
fi

stop_port_forwards
section "Removing the disposable Kravel demo installation"
kubectl delete namespace kravel-demo kravel-system kravel-observability --ignore-not-found --wait=true
kubectl delete clusterrole kravel-debugger-readonly kravel-local-observer --ignore-not-found
kubectl delete clusterrolebinding kravel-debugger-readonly kravel-local-observer --ignore-not-found
rm -f -- "$KRAVEL_STATE"
rm -f -- "$KRAVEL_LEGACY_STATE"
printf 'Full reset complete. Local container images and the Qwen model remain cached.\n'
