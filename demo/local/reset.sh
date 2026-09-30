#!/usr/bin/env bash
set -Eeuo pipefail
. "$(dirname "$0")/common.sh"

full=false
while (($#)); do case "$1" in --full|-Full) full=true; shift ;; *) die "Unknown option: $1" ;; esac; done
assert_prerequisites
stop_port_forwards
section "Removing disposable Kravel demo resources"
kubectl delete namespace kravel-demo kravel-system kravel-observability --ignore-not-found --wait=true
kubectl delete clusterrole kravel-local-observer --ignore-not-found
kubectl delete clusterrolebinding kravel-local-observer --ignore-not-found
if [[ "$full" == true ]]; then kubectl delete namespace kravel-ai --ignore-not-found --wait=true; fi
rm -f -- "$KRAVEL_STATE"
rm -f -- "$KRAVEL_LEGACY_STATE"
printf 'Reset complete. Docker images and Qwen remain cached.\n'
