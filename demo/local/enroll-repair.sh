#!/usr/bin/env bash
# Generate explicit named capabilities. No apply, patch, reset, or model call.
set -Eeuo pipefail
. "$(dirname "$0")/common.sh"
kind="${1:-}"; name="${2:-}"; scenario="${3:-}"
[[ -n "$kind" && -n "$name" && -n "$scenario" ]] || die "Usage: bash demo/local/enroll-repair.sh KIND NAME SCENARIO [--container NAME] [--image VERIFIED_IMAGE]"
shift 3
case "$kind" in deployments|daemonsets|services|configmaps) ;; *) die "Supported kinds: deployments, daemonsets, services, configmaps" ;; esac
[[ "$name" =~ ^[a-z0-9][-a-z0-9.]*$ ]] || die "Use an explicit resource name, not flags or a wildcard."
[[ "$(kubectl config current-context)" == "docker-desktop" ]] || die "Enrollment helper is restricted to docker-desktop."
mkdir -p "$KRAVEL_ROOT/data"
mount_path="$KRAVEL_ROOT/data"
if command -v cygpath >/dev/null; then mount_path="$(cygpath -m "$mount_path")"; fi
kubectl -n kravel-demo get "$kind" "$name" -o json |
  MSYS_NO_PATHCONV=1 docker run --rm -i --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --mount "type=bind,source=$mount_path,target=/policy" --entrypoint python kravel:local \
    -m kravel.remediation --scenario "$scenario" --policy /policy/repair-profiles.json --output /policy/repair-enrollment.json "$@"
printf '\nReview data/repair-enrollment.json, especially its patch and resourceNames.\n'
printf 'If correct, enroll it yourself: kubectl apply -f data/repair-enrollment.json\n'
printf 'Then refresh the mounted policy: kubectl -n kravel-system rollout restart deployment/kravel deployment/kravel-approval-broker\n'
printf 'Karl remains read-only. The helper has not changed your cluster.\n'
