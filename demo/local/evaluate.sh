#!/usr/bin/env bash
set -Eeuo pipefail
. "$(dirname "$0")/common.sh"
assert_prerequisites
profile="${1:-quick}"
[[ "$profile" == quick || "$profile" == all ]] || die "Usage: bash demo/local/evaluate.sh [quick|all] [investigation-id]"
args=(--profile "$profile" --wait)
[[ -n "${2:-}" ]] && args+=(--run-id "$2")
kubectl -n kravel-system exec deployment/kravel -- python -m kravel.cli evaluate "${args[@]}"
