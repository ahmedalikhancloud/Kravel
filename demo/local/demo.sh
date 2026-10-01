#!/usr/bin/env bash
set -Eeuo pipefail
. "$(dirname "$0")/common.sh"

connect_only=false
case "${1:-}" in
  --connect-only) connect_only=true ;;
  '') ;;
  *) die "Usage: bash demo/local/demo.sh [--connect-only]" ;;
esac

assert_prerequisites
qwen_profile
stop_port_forwards
rm -f -- "$KRAVEL_STATE"

if [[ "$connect_only" == false ]]; then
  section "Resetting the disposable demo namespace"
  kubectl delete namespace kravel-demo --ignore-not-found --wait=true
  kubectl create namespace kravel-demo
  kubectl apply -f "$KRAVEL_ROOT/deploy/local.yaml"
  configure_kravel_model
  rollout kravel-system kravel 4m
  rollout kravel-system kravel-approval-broker 4m
  rollout kravel-system kravel-operator 4m

  section "Creating five healthy, independently breakable labs"
  kubectl apply -f "$KRAVEL_ROOT/demo/local/labs.yaml"
  for deployment in oom-demo image-demo crash-demo config-demo net-demo; do rollout kravel-demo "$deployment" 3m; done
fi

section "Binding the debugger, Local Slack, Grafana, and MLflow to localhost only"
kravel_ui_pid="$(start_port_forward kravel-system kravel 8080 8080)"
approval_pid="$(start_port_forward kravel-system kravel-approval-broker 8081 8090)"
grafana_pid="$(start_port_forward kravel-observability kravel-grafana 3000 3000)"
mlflow_pid="$(start_port_forward kravel-observability kravel-mlflow 5000 5000)"
approval_token="$(kubectl -n kravel-system get secret kravel-approval-token -o jsonpath='{.data.token}' | base64 --decode)"
approval_url="http://127.0.0.1:8081/?token=$approval_token"
printf 'KRAVEL_UI_PID=%s\nAPPROVAL_PID=%s\nGRAFANA_PID=%s\nMLFLOW_PID=%s\nAPPROVAL_URL=%s\n' "$kravel_ui_pid" "$approval_pid" "$grafana_pid" "$mlflow_pid" "$approval_url" > "$KRAVEL_STATE"

wait_until "Kravel debugger localhost:8080" 30 curl -fsS --output /dev/null http://127.0.0.1:8080/readyz
wait_until "Local Slack localhost:8081" 30 curl -fsS --output /dev/null http://127.0.0.1:8081/readyz
wait_until "Grafana localhost:3000" 30 curl -fsS --output /dev/null http://127.0.0.1:3000/api/health
wait_until "MLflow localhost:5000" 30 curl -fsS --output /dev/null http://127.0.0.1:5000/health
if [[ "$connect_only" == false ]]; then fresh_demo_view; fi

printf '\nDemo ready.\n'
printf 'Kravel debugger: http://127.0.0.1:8080\n'
printf 'Local Slack approval inbox: %s\n' "$approval_url"
printf 'Grafana: http://127.0.0.1:3000\n'
printf 'MLflow traces: http://127.0.0.1:5000\n\n'
printf 'Follow the guided practice cards on the Kravel page. No embedded console is needed.\n\n'
printf 'Break one lab: bash demo/local/scenario.sh break oom\n'
printf 'Other choices: imagepull | crashloop | configmap | network | all\n'
printf 'Reset healthy: bash demo/local/reset.sh\n'
