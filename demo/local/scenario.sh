#!/usr/bin/env bash
set -Eeuo pipefail
. "$(dirname "$0")/common.sh"

action="${1:-status}"
scenario="${2:-all}"
assert_prerequisites
kubectl get namespace kravel-demo >/dev/null 2>&1 || die "Run bash demo/local/demo.sh first."

break_scenario() {
  case "$1" in
    oom)
      section "Breaking oom-demo: allocate 96Mi inside a 32Mi container"
      kubectl -n kravel-demo patch deployment oom-demo --type strategic -p '{"spec":{"template":{"spec":{"containers":[{"name":"oom-demo","resources":{"limits":{"memory":"32Mi"}},"command":["awk"],"args":["BEGIN { for (i = 0; i < 96; i++) blocks[i] = sprintf(\"%1048576s\", \"x\"); while (1) system(\"sleep 3600\") }"]}]}}}}'
      ;;
    imagepull)
      section "Breaking image-demo: roll out a nonexistent image"
      kubectl -n kravel-demo patch deployment image-demo --type strategic -p '{"spec":{"template":{"spec":{"containers":[{"name":"image-demo","image":"busybox:kravel-demo-image-does-not-exist","imagePullPolicy":"Always"}]}}}}'
      ;;
    crashloop)
      section "Breaking crash-demo: exit with an application error"
      kubectl -n kravel-demo patch deployment crash-demo --type strategic -p '{"spec":{"template":{"spec":{"containers":[{"name":"crash-demo","command":["sh","-c"],"args":["echo FATAL: simulated startup dependency failure >&2; exit 42"]}]}}}}'
      ;;
    configmap)
      section "Breaking config-demo: publish an invalid ConfigMap value"
      kubectl -n kravel-demo patch configmap config-demo --type merge -p '{"data":{"MODE":"broken"}}'
      local marker
      marker="$(timestamp)"
      kubectl -n kravel-demo patch deployment config-demo --type merge -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"kravel.dev/approved-restart\":null,\"kravel.dev/scenario-broken-at\":\"$marker\"}}}}}"
      ;;
    network)
      section "Breaking demo-gateway: Service selector matches no Pods"
      kubectl -n kravel-demo patch service demo-gateway --type merge -p '{"spec":{"selector":{"app":"no-such-demo-app"}}}'
      ;;
    all)
      for item in oom imagepull crashloop configmap network; do break_scenario "$item"; done
      ;;
    *) die "Scenario must be oom, imagepull, crashloop, configmap, network, or all" ;;
  esac
}

reset_scenario() {
  case "$1" in
    oom)
      kubectl -n kravel-demo patch deployment oom-demo --type strategic -p '{"spec":{"template":{"spec":{"containers":[{"name":"oom-demo","resources":{"limits":{"memory":"32Mi"}},"command":["sh","-c"],"args":["exec sleep 86400"]}]}}}}'
      ;;
    imagepull)
      kubectl -n kravel-demo patch deployment image-demo --type strategic -p '{"spec":{"template":{"spec":{"containers":[{"name":"image-demo","image":"busybox:1.36","imagePullPolicy":"IfNotPresent"}]}}}}'
      ;;
    crashloop)
      kubectl -n kravel-demo patch deployment crash-demo --type strategic -p '{"spec":{"template":{"spec":{"containers":[{"name":"crash-demo","command":["sh","-c"],"args":["exec sleep 86400"]}]}}}}'
      ;;
    configmap)
      kubectl -n kravel-demo patch configmap config-demo --type merge -p '{"data":{"MODE":"healthy"}}'
      kubectl -n kravel-demo rollout restart deployment/config-demo
      ;;
    network)
      kubectl -n kravel-demo patch service demo-gateway --type merge -p '{"spec":{"selector":{"app":"net-demo"}}}'
      ;;
    all)
      kubectl apply -f "$KRAVEL_ROOT/demo/local/labs.yaml"
      kubectl -n kravel-demo patch deployment config-demo --type merge -p '{"spec":{"template":{"metadata":{"annotations":{"kravel.dev/approved-restart":null,"kravel.dev/scenario-broken-at":null}}}}}'
      kubectl -n kravel-demo rollout restart deployment/config-demo
      ;;
    *) die "Scenario must be oom, imagepull, crashloop, configmap, network, or all" ;;
  esac
  section "Waiting for the reset workloads"
  local targets
  case "$1" in
    oom) targets='oom-demo' ;;
    imagepull) targets='image-demo' ;;
    crashloop) targets='crash-demo' ;;
    configmap) targets='config-demo' ;;
    network) targets='net-demo' ;;
    all) targets='oom-demo image-demo crash-demo config-demo net-demo' ;;
  esac
  for deployment in $targets; do rollout kravel-demo "$deployment" 3m; done
}

case "$action" in
  break) break_scenario "$scenario" ;;
  reset) reset_scenario "$scenario" ;;
  status) ;;
  *) die "Usage: bash demo/local/scenario.sh break|reset|status [oom|imagepull|crashloop|configmap|network|all]" ;;
esac

section "Current demo status"
kubectl -n kravel-demo get pods -o wide
printf '\nRecent warnings:\n'
kubectl -n kravel-demo get events --field-selector type=Warning --sort-by=.lastTimestamp | tail -n 12 || true
printf '\nRefresh Kravel: http://127.0.0.1:8080\n'
