[CmdletBinding()]
param(
  [ValidateRange(1, 10)][int]$Runs = 1
)

. (Join-Path $PSScriptRoot "common.ps1")
Assert-KravelPrerequisites

if (-not (Test-Path -LiteralPath $script:KravelStatePath)) {
  throw "No local demo state was found. Run .\demo\local\demo.cmd first."
}
$state = Get-Content -LiteralPath $script:KravelStatePath -Raw | ConvertFrom-Json

for ($run = 1; $run -le $Runs; $run += 1) {
  Write-Host "`n==> Incident pipeline run $run of $Runs"
  Invoke-KravelNative "kubectl" @(
    "-n", "kravel-system", "exec", "deployment/kravel", "--",
    "node", "src/pipeline-cli.mjs",
    "--baseline", [string]$state.baselineAt,
    "--incident", [string]$state.incidentAt,
    "--namespace", "kravel-demo",
    "--scenario", [string]$state.scenario
  )
}

Write-Host "`nRefresh Grafana at http://localhost:3000 and MLflow at http://localhost:5000."
