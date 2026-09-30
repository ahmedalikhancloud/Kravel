#!/usr/bin/env bash
set -Eeuo pipefail
. "$(dirname "$0")/common.sh"

scenario=escalation
runs=1
while (($#)); do
  case "$1" in
    --scenario|-Scenario) scenario="$2"; shift 2 ;;
    --runs|-Runs) runs="$2"; shift 2 ;;
    *) die "Unknown option: $1" ;;
  esac
done
[[ "$scenario" == routine || "$scenario" == escalation ]] || die "Scenario must be routine or escalation"
assert_prerequisites
qwen_profile
stop_port_forwards
rollout kravel-ai kravel-laya 60s
docker model run --detach "$QWEN_MODEL"

section "Resetting disposable demo resources"
kubectl delete namespace kravel-demo kravel-system kravel-observability --ignore-not-found --wait=true
kubectl delete clusterrole kravel-local-observer --ignore-not-found
kubectl delete clusterrolebinding kravel-local-observer --ignore-not-found

section "Starting Python Kravel, Prometheus, Grafana, and MLflow"
kubectl apply -f "$KRAVEL_ROOT/deploy/local.yaml"
configure_kravel_model
kubectl apply -f "$KRAVEL_ROOT/deploy/observability-local.yaml"
rollout kravel-system kravel 4m
for deployment in kravel-prometheus kravel-grafana kravel-mlflow; do rollout kravel-observability "$deployment" 6m; done

section "Creating the healthy workload baseline"
kubectl apply -f "$KRAVEL_ROOT/demo/local/lab.yaml"
rollout kravel-demo checkout-api 3m
if [[ "$scenario" == escalation ]]; then
  kubectl apply -f "$KRAVEL_ROOT/demo/local/multi-lab.yaml"
  for deployment in payments-api inventory-api reports-worker; do rollout kravel-demo "$deployment" 3m; done
  wait_until "payments-api endpoints" 60 payments_endpoints_present
fi
wait_resource ConfigMap api-config
wait_resource Deployment checkout-api
if [[ "$scenario" == escalation ]]; then
  wait_resource Service payments-api; wait_resource Deployment inventory-api; wait_resource Deployment reports-worker
fi
baseline_at="$(timestamp)"
printf 'Healthy baseline: %s\n' "$baseline_at"

section "Incident 1: ConfigMap regression causes CrashLoopBackOff"
change_at="$(timestamp)"
kubectl -n kravel-demo patch configmap api-config --type merge -p "{\"metadata\":{\"annotations\":{\"kravel.dev/change-at\":\"$change_at\",\"kravel.dev/incident\":\"config-regression\"}},\"data\":{\"STARTUP_MODE\":\"broken\",\"DATABASE_TIMEOUT_MS\":\"3\",\"CHANGE_TICKET\":\"INC-4242\"}}"
kubectl -n kravel-demo rollout restart deployment/checkout-api
wait_pod_reason app=checkout-api 'CrashLoopBackOff|Error' 90
wait_resource ConfigMap api-config "$change_at"

if [[ "$scenario" == escalation ]]; then
  section "Incident 2: Service selector drift removes every payments endpoint"
  change_at="$(timestamp)"
  kubectl -n kravel-demo patch service payments-api --type merge -p "{\"metadata\":{\"annotations\":{\"kravel.dev/change-at\":\"$change_at\",\"kravel.dev/incident\":\"service-selector-drift\"}},\"spec\":{\"selector\":{\"app\":\"payments-api-v2\"}}}"
  wait_until "payments-api to have zero endpoints" 60 payments_endpoints_absent
  wait_resource Service payments-api "$change_at"

  section "Incident 3: Inventory rolls out a nonexistent image"
  change_at="$(timestamp)"
  kubectl -n kravel-demo patch deployment inventory-api --type strategic -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"kravel.dev/change-at\":\"$change_at\",\"kravel.dev/incident\":\"bad-image-rollout\"}},\"spec\":{\"containers\":[{\"name\":\"inventory-api\",\"image\":\"busybox:kravel-demo-image-does-not-exist\"}]}}}}"
  wait_pod_reason app=inventory-api 'ErrImagePull|ImagePullBackOff' 120
  wait_resource Deployment inventory-api "$change_at"

  section "Incident 4: Reports receives an impossible node selector"
  change_at="$(timestamp)"
  kubectl -n kravel-demo patch deployment reports-worker --type strategic -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"kravel.dev/change-at\":\"$change_at\",\"kravel.dev/incident\":\"impossible-node-selector\"}},\"spec\":{\"nodeSelector\":{\"kravel.dev/nonexistent-node\":\"true\"}}}}}"
  wait_until "reports-worker to become unschedulable" 90 reports_unschedulable
  wait_resource Deployment reports-worker "$change_at"
fi

incident_at="$(timestamp)"
printf 'SCENARIO=%s\nBASELINE_AT=%s\nINCIDENT_AT=%s\nGRAFANA_PID=\nMLFLOW_PID=\n' "$scenario" "$baseline_at" "$incident_at" > "$KRAVEL_STATE"

section "Deterministic reconstruction before AI analysis"
kubectl -n kravel-system exec deployment/kravel -- python -m kravel.cli report --baseline "$baseline_at" --incident "$incident_at" --namespace kravel-demo
"$KRAVEL_ROOT/demo/local/run-pipeline.sh" --runs "$runs"

section "Binding dashboards to localhost only"
grafana_pid="$(start_port_forward kravel-observability kravel-grafana 3000 3000)"
mlflow_pid="$(start_port_forward kravel-observability kravel-mlflow 5000 5000)"
sed -i "s/^GRAFANA_PID=.*/GRAFANA_PID=$grafana_pid/;s/^MLFLOW_PID=.*/MLFLOW_PID=$mlflow_pid/" "$KRAVEL_STATE"
wait_until "Grafana localhost:3000" 30 curl -fsS http://127.0.0.1:3000/api/health
wait_until "MLflow localhost:5000" 30 curl -fsS http://127.0.0.1:5000/health
printf '\nDemo ready.\nGrafana: http://localhost:3000\nMLflow traces: http://localhost:5000\nCleanup: bash demo/local/reset.sh\n'
