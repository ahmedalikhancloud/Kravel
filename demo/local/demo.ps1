[CmdletBinding()]
param(
  [ValidateSet("routine", "escalation")][string]$Scenario = "escalation",
  [ValidateRange(1, 10)][int]$Runs = 1
)

. (Join-Path $PSScriptRoot "common.ps1")

function Invoke-DemoPatch {
  param(
    [Parameter(Mandatory = $true)][string]$Resource,
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][object]$Patch,
    [ValidateSet("merge", "strategic")][string]$Type = "merge"
  )
  $temporaryPath = [IO.Path]::GetTempFileName()
  try {
    $json = $Patch | ConvertTo-Json -Depth 20 -Compress
    [IO.File]::WriteAllText($temporaryPath, $json, (New-Object Text.UTF8Encoding($false)))
    Invoke-KravelNative "kubectl" @("-n", "kravel-demo", "patch", $Resource, $Name, "--type", $Type, "--patch-file", $temporaryPath)
  } finally {
    if (Test-Path -LiteralPath $temporaryPath) { Remove-Item -LiteralPath $temporaryPath -Force }
  }
}

function Wait-ForPodReason {
  param([string]$Selector, [string]$Pattern, [int]$TimeoutSeconds = 120)
  Wait-KravelCondition "Pod reason matching $Pattern" {
    $reasons = (& kubectl -n kravel-demo get pods -l $Selector -o "jsonpath={.items[*].status.containerStatuses[*].state.waiting.reason}" 2>$null | Out-String)
    return $reasons -match $Pattern
  } $TimeoutSeconds
}

function Wait-ForKravelResource {
  param(
    [Parameter(Mandatory = $true)][string]$Kind,
    [Parameter(Mandatory = $true)][string]$Name,
    [string]$ChangeAt = "",
    [int]$TimeoutSeconds = 90
  )
  $probe = @'
const [kind, name, changeAt] = process.argv.slice(1);
const timestamp = encodeURIComponent(new Date().toISOString());
fetch(`http://127.0.0.1:8080/v1/state/rewind?timestamp=${timestamp}&namespace=kravel-demo`)
  .then((response) => response.json())
  .then((state) => {
    const object = state.objects?.find((candidate) => candidate.kind === kind && candidate.metadata?.name === name);
    const annotationValues = Object.values(object?.metadata?.annotations ?? {});
    const observed = object && (!changeAt || annotationValues.includes(changeAt));
    console.log(Boolean(observed));
  })
  .catch(() => console.log(false));
'@
  $description = "Kravel to record $Kind kravel-demo/$Name"
  if ($ChangeAt) { $description += " at $ChangeAt" }
  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  while ([DateTime]::UtcNow -lt $deadline) {
    $output = & kubectl -n kravel-system exec deployment/kravel -- node -e $probe $Kind $Name $ChangeAt 2>$null
    $exitCode = $LASTEXITCODE
    $observed = ($output | Out-String).Trim()
    if ($exitCode -eq 0 -and $observed -eq "true") { return }
    Start-Sleep -Seconds 2
  }
  throw "Timed out waiting for $description"
}

Assert-KravelPrerequisites
Stop-KravelPortForwards

Invoke-KravelNative "kubectl" @("-n", "kravel-ai", "get", "deployment/kravel-laya")
Invoke-KravelNative "kubectl" @("-n", "kravel-ai", "rollout", "status", "deployment/kravel-laya", "--timeout=30s")
Invoke-KravelNative "docker" @("model", "run", "--detach", "ai/qwen3:4b-thinking-2507-q4_K_M")

Write-Host "`n==> Resetting only disposable demo namespaces"
Invoke-KravelNative "kubectl" @("delete", "namespace", "kravel-demo", "kravel-system", "kravel-observability", "--ignore-not-found", "--wait=true")
Invoke-KravelNative "kubectl" @("delete", "clusterrole", "kravel-local-observer", "--ignore-not-found")
Invoke-KravelNative "kubectl" @("delete", "clusterrolebinding", "kravel-local-observer", "--ignore-not-found")

Write-Host "`n==> Starting Kravel, Prometheus, Grafana, and MLflow"
Invoke-KravelNative "kubectl" @("apply", "-f", (Join-Path $script:KravelRoot "deploy\local.yaml"))
Invoke-KravelNative "kubectl" @("apply", "-f", (Join-Path $script:KravelRoot "deploy\observability-local.yaml"))
Invoke-KravelNative "kubectl" @("-n", "kravel-system", "rollout", "status", "deployment/kravel", "--timeout=3m")
foreach ($deployment in @("kravel-prometheus", "kravel-grafana", "kravel-mlflow")) {
  try {
    Invoke-KravelNative "kubectl" @("-n", "kravel-observability", "rollout", "status", "deployment/$deployment", "--timeout=5m")
  } catch {
    Show-KravelDeploymentDiagnostics `
      -Namespace "kravel-observability" `
      -Deployment $deployment `
      -Container ($deployment -replace "^kravel-", "")
    throw
  }
}

Write-Host "`n==> Creating the healthy workload baseline"
Invoke-KravelNative "kubectl" @("apply", "-f", (Join-Path $PSScriptRoot "lab.yaml"))
Invoke-KravelNative "kubectl" @("-n", "kravel-demo", "rollout", "status", "deployment/checkout-api", "--timeout=3m")
if ($Scenario -eq "escalation") {
  Invoke-KravelNative "kubectl" @("apply", "-f", (Join-Path $PSScriptRoot "multi-lab.yaml"))
  foreach ($deployment in @("payments-api", "inventory-api", "reports-worker")) {
    Invoke-KravelNative "kubectl" @("-n", "kravel-demo", "rollout", "status", "deployment/$deployment", "--timeout=3m")
  }
  Wait-KravelCondition "payments-api endpoints" {
    $addresses = (& kubectl -n kravel-demo get endpoints payments-api -o "jsonpath={.subsets[*].addresses[*].ip}" 2>$null | Out-String).Trim()
    return [bool]$addresses
  } 60
}
Wait-ForKravelResource "ConfigMap" "api-config"
Wait-ForKravelResource "Deployment" "checkout-api"
if ($Scenario -eq "escalation") {
  Wait-ForKravelResource "Service" "payments-api"
  Wait-ForKravelResource "Deployment" "inventory-api"
  Wait-ForKravelResource "Deployment" "reports-worker"
}
$baselineAt = Get-KravelTimestamp
Write-Host "Healthy baseline captured at $baselineAt"

Write-Host "`n==> Incident 1: ConfigMap regression causes checkout CrashLoopBackOff"
$changeAt = Get-KravelTimestamp
Invoke-DemoPatch "configmap" "api-config" @{
  metadata = @{ annotations = @{ "kravel.dev/change-at" = $changeAt; "kravel.dev/incident" = "config-regression" } }
  data = @{ STARTUP_MODE = "broken"; DATABASE_TIMEOUT_MS = "3"; CHANGE_TICKET = "INC-4242" }
}
Invoke-KravelNative "kubectl" @("-n", "kravel-demo", "rollout", "restart", "deployment/checkout-api")
Wait-ForPodReason "app=checkout-api" "CrashLoopBackOff|Error" 90
Wait-ForKravelResource "ConfigMap" "api-config" $changeAt

if ($Scenario -eq "escalation") {
  Write-Host "`n==> Incident 2: Service selector drift removes every payments endpoint"
  $changeAt = Get-KravelTimestamp
  Invoke-DemoPatch "service" "payments-api" @{
    metadata = @{ annotations = @{ "kravel.dev/change-at" = $changeAt; "kravel.dev/incident" = "service-selector-drift" } }
    spec = @{ selector = @{ app = "payments-api-v2" } }
  }
  Wait-KravelCondition "payments-api to have zero endpoints" {
    $addresses = (& kubectl -n kravel-demo get endpoints payments-api -o "jsonpath={.subsets[*].addresses[*].ip}" 2>$null | Out-String).Trim()
    return -not [bool]$addresses
  } 60
  Wait-ForKravelResource "Service" "payments-api" $changeAt

  Write-Host "`n==> Incident 3: Inventory rolls out an image that does not exist"
  $changeAt = Get-KravelTimestamp
  Invoke-DemoPatch "deployment" "inventory-api" @{
    spec = @{ template = @{
      metadata = @{ annotations = @{ "kravel.dev/change-at" = $changeAt; "kravel.dev/incident" = "bad-image-rollout" } }
      spec = @{ containers = @(@{ name = "inventory-api"; image = "busybox:kravel-demo-image-does-not-exist" }) }
    } }
  } "strategic"
  Wait-ForPodReason "app=inventory-api" "ErrImagePull|ImagePullBackOff" 120
  Wait-ForKravelResource "Deployment" "inventory-api" $changeAt

  Write-Host "`n==> Incident 4: Reports receives an impossible node selector"
  $changeAt = Get-KravelTimestamp
  Invoke-DemoPatch "deployment" "reports-worker" @{
    spec = @{ template = @{
      metadata = @{ annotations = @{ "kravel.dev/change-at" = $changeAt; "kravel.dev/incident" = "impossible-node-selector" } }
      spec = @{ nodeSelector = @{ "kravel.dev/nonexistent-node" = "true" } }
    } }
  } "strategic"
  Wait-KravelCondition "reports-worker to become unschedulable" {
    $reason = (& kubectl -n kravel-demo get pods -l app=reports-worker -o "jsonpath={.items[*].status.conditions[?(@.type=='PodScheduled')].reason}" 2>$null | Out-String)
    return $reason -match "Unschedulable"
  } 90
  Wait-ForKravelResource "Deployment" "reports-worker" $changeAt
}

$incidentAt = Get-KravelTimestamp
$state = [ordered]@{
  scenario = $Scenario
  baselineAt = $baselineAt
  incidentAt = $incidentAt
  grafanaPid = $null
  mlflowPid = $null
}
$state | ConvertTo-Json | Set-Content -LiteralPath $script:KravelStatePath -Encoding UTF8

Write-Host "`n==> Running the guarded incident pipeline"
& (Join-Path $PSScriptRoot "run-pipeline.ps1") -Runs $Runs

Write-Host "`n==> Binding dashboards to localhost only"
$grafana = Start-KravelPortForward "kravel-observability" "kravel-grafana" 3000 3000
$mlflow = Start-KravelPortForward "kravel-observability" "kravel-mlflow" 5000 5000
Wait-KravelTcpPort 3000 30
Wait-KravelTcpPort 5000 30
$state.grafanaPid = $grafana.Id
$state.mlflowPid = $mlflow.Id
$state | ConvertTo-Json | Set-Content -LiteralPath $script:KravelStatePath -Encoding UTF8

Write-Host "`nDemo ready."
Write-Host "Grafana: http://localhost:3000"
Write-Host "MLflow:  http://localhost:5000"
Write-Host "Rerun the same incident window: .\demo\local\run-pipeline.cmd -Runs 3"
Write-Host "Cleanup: .\demo\local\reset.cmd"
