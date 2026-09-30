[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot "common.ps1")

$qwenModel = "ai/qwen3:4b-thinking-2507-q4_K_M"
Assert-KravelPrerequisites

Write-Host "`n==> Verifying Docker Model Runner"
Select-KravelDesktopModelRunner

Write-Host "`n==> Downloading and configuring the local Qwen reasoning model"
Invoke-KravelNativeWithRetry "docker" @("model", "pull", $qwenModel)
Invoke-KravelNative "docker" @("model", "configure", "--context-size", "4096", $qwenModel)
Invoke-KravelNative "docker" @("model", "run", "--detach", $qwenModel)

Write-Host "`n==> Building the local Kravel and CPU-only Laya images"
Invoke-KravelNative "docker" @("build", "--tag", "kravel:local", $script:KravelRoot)
Invoke-KravelNative "docker" @("build", "--tag", "kravel-laya:local", "--file", (Join-Path $PSScriptRoot "laya.Dockerfile"), $script:KravelRoot)

Write-Host "`n==> Caching the synthetic workload and observability images"
foreach ($image in @("busybox:1.36", "prom/prometheus:v3.13.3", "grafana/grafana:13.1.6", "ghcr.io/mlflow/mlflow:v3.14.0")) {
  Invoke-KravelNativeWithRetry "docker" @("pull", $image)
}

Write-Host "`n==> Starting Laya and downloading its English checkpoint into a persistent local volume"
Invoke-KravelNative "kubectl" @("apply", "-f", (Join-Path $script:KravelRoot "deploy\laya-local.yaml"))
Invoke-KravelNative "kubectl" @("-n", "kravel-ai", "rollout", "restart", "deployment/kravel-laya")
Invoke-KravelNative "kubectl" @("-n", "kravel-ai", "rollout", "status", "deployment/kravel-laya", "--timeout=10m")

Write-Host "`n==> Preloading Kravel and the local observability stack"
Invoke-KravelNative "kubectl" @("apply", "-f", (Join-Path $script:KravelRoot "deploy\local.yaml"))
Invoke-KravelNative "kubectl" @("apply", "-f", (Join-Path $script:KravelRoot "deploy\observability-local.yaml"))
Invoke-KravelNative "kubectl" @("-n", "kravel-system", "rollout", "restart", "deployment/kravel")
Invoke-KravelNative "kubectl" @("-n", "kravel-system", "rollout", "status", "deployment/kravel", "--timeout=3m")
foreach ($deployment in @("kravel-prometheus", "kravel-grafana", "kravel-mlflow")) {
  Invoke-KravelNative "kubectl" @("-n", "kravel-observability", "rollout", "status", "deployment/$deployment", "--timeout=5m")
}

Write-Host "`n==> Verifying that a Kubernetes Pod can reach Docker Model Runner"
$probe = "fetch('http://model-runner.docker.internal/engines/v1/models').then(async r=>{if(!r.ok)throw new Error('HTTP '+r.status+' '+await r.text());console.log('Qwen endpoint reachable')}).catch(e=>{console.error(e.message);process.exit(1)})"
Invoke-KravelNative "kubectl" @("-n", "kravel-system", "exec", "deployment/kravel", "--", "node", "-e", $probe)

Write-Host "`nPreparation complete. Models and container images are cached locally."
Write-Host "Run the demo with: .\demo\local\demo.cmd -Scenario escalation"
