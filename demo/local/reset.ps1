[CmdletBinding()]
param(
  [switch]$Full
)

. (Join-Path $PSScriptRoot "common.ps1")
Assert-KravelPrerequisites
Stop-KravelPortForwards

Write-Host "`n==> Removing disposable Kravel demo resources"
Invoke-KravelNative "kubectl" @("delete", "namespace", "kravel-demo", "kravel-system", "kravel-observability", "--ignore-not-found", "--wait=true")
Invoke-KravelNative "kubectl" @("delete", "clusterrole", "kravel-local-observer", "--ignore-not-found")
Invoke-KravelNative "kubectl" @("delete", "clusterrolebinding", "kravel-local-observer", "--ignore-not-found")

if ($Full) {
  Write-Host "`n==> Removing Laya and its cached checkpoint because -Full was supplied"
  Invoke-KravelNative "kubectl" @("delete", "namespace", "kravel-ai", "--ignore-not-found", "--wait=true")
}

if (Test-Path -LiteralPath $script:KravelStatePath) {
  Remove-Item -LiteralPath $script:KravelStatePath -Force
}
Write-Host "Reset complete. Docker images and the Qwen model remain cached locally."
