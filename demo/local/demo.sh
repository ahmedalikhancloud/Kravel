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

section "Binding the debugger, human demo controls, Local Slack, Grafana, and MLflow to localhost only"
kravel_ui_pid="$(start_port_forward kravel-system kravel 8080 8080)"
approval_pid="$(start_port_forward kravel-system kravel-approval-broker 8081 8090)"
grafana_pid="$(start_port_forward kravel-observability kravel-grafana 3000 3000)"
mlflow_pid="$(start_port_forward kravel-observability kravel-mlflow 5000 5000)"
operator_pid="$(start_port_forward kravel-system kravel-operator 8082 8082)"
approval_token="$(kubectl -n kravel-system get secret kravel-approval-token -o jsonpath='{.data.token}' | base64 --decode)"
approval_url="http://127.0.0.1:8081/?token=$approval_token"
operator_token="$(kubectl -n kravel-system get secret kravel-operator-token -o jsonpath='{.data.token}' | base64 --decode)"
operator_url="http://127.0.0.1:8082/#token=$operator_token"
printf 'KRAVEL_UI_PID=%s\nAPPROVAL_PID=%s\nGRAFANA_PID=%s\nMLFLOW_PID=%s\nOPERATOR_PID=%s\nAPPROVAL_URL=%s\nOPERATOR_URL=%s\n' "$kravel_ui_pid" "$approval_pid" "$grafana_pid" "$mlflow_pid" "$operator_pid" "$approval_url" "$operator_url" > "$KRAVEL_STATE"
chmod 600 "$KRAVEL_STATE" 2>/dev/null || true
unset operator_token approval_token

wait_until "Kravel debugger localhost:8080" 30 curl -fsS --output /dev/null http://127.0.0.1:8080/readyz
wait_until "Local Slack localhost:8081" 30 curl -fsS --output /dev/null http://127.0.0.1:8081/readyz
wait_until "Grafana localhost:3000" 30 curl -fsS --output /dev/null http://127.0.0.1:3000/api/health
wait_until "MLflow localhost:5000" 30 curl -fsS --output /dev/null http://127.0.0.1:5000/health
wait_until "Human demo controls localhost:8082" 30 curl -fsS --output /dev/null http://127.0.0.1:8082/readyz
if [[ "$connect_only" == false ]]; then fresh_demo_view; fi

printf '\nDemo ready.\n'
printf 'Kravel debugger: http://127.0.0.1:8080\n'
printf 'Local Slack approval inbox: %s\n' "$approval_url"
printf 'Demo controls (private unlock link, open once): %s\n' "$operator_url"
printf 'Grafana: http://127.0.0.1:3000\n'
printf 'MLflow traces: http://127.0.0.1:5000\n\n'
printf 'Open your private Demo controls link once, then reload Kravel. Each practice card has a button.\n'
printf 'Treat private unlock/inbox links as passwords; do not show them on recordings.\n\n'
printf 'Break one lab: bash demo/local/scenario.sh break oom\n'
printf 'Other choices: imagepull | crashloop | configmap | network | all\n'
printf 'Reset healthy: bash demo/local/reset.sh\n'
