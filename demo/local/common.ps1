Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:KravelRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$script:KravelStatePath = Join-Path $script:KravelRoot ".kravel-local-state.json"

function Invoke-KravelNative {
  param(
    [Parameter(Mandatory = $true)][string]$Command,
    [Parameter(Mandatory = $true)][string[]]$Arguments
  )
  & $Command @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $Command $($Arguments -join ' ')"
  }
}

function Assert-KravelPrerequisites {
  foreach ($name in @("docker", "kubectl")) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
      throw "$name is required. Complete the one-time setup in DEMO.md first."
    }
  }
  Invoke-KravelNative "docker" @("info")
  $context = (& kubectl config current-context 2>$null | Out-String).Trim()
  if ($LASTEXITCODE -ne 0 -or $context -ne "docker-desktop") {
    throw "Refusing to modify Kubernetes context '$context'. Switch to the Docker Desktop context: kubectl config use-context docker-desktop"
  }
  Invoke-KravelNative "kubectl" @("wait", "--for=condition=Ready", "node", "--all", "--timeout=180s")
}

function Wait-KravelCondition {
  param(
    [Parameter(Mandatory = $true)][string]$Description,
    [Parameter(Mandatory = $true)][scriptblock]$Condition,
    [int]$TimeoutSeconds = 120
  )
  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  while ([DateTime]::UtcNow -lt $deadline) {
    if (& $Condition) { return }
    Start-Sleep -Seconds 2
  }
  throw "Timed out waiting for $Description"
}

function Stop-KravelPortForwards {
  if (-not (Test-Path -LiteralPath $script:KravelStatePath)) { return }
  try {
    $state = Get-Content -LiteralPath $script:KravelStatePath -Raw | ConvertFrom-Json
    foreach ($property in @("grafanaPid", "mlflowPid")) {
      $processId = $state.$property
      if ($processId -and $processId -as [int]) {
        $process = Get-Process -Id ([int]$processId) -ErrorAction SilentlyContinue
        if ($process -and $process.ProcessName -match "kubectl") {
          Stop-Process -Id $process.Id -Force
        }
      }
    }
  } catch {
    Write-Warning "Could not inspect the previous port-forward state: $($_.Exception.Message)"
  }
}

function Start-KravelPortForward {
  param(
    [Parameter(Mandatory = $true)][string]$Namespace,
    [Parameter(Mandatory = $true)][string]$Service,
    [Parameter(Mandatory = $true)][int]$LocalPort,
    [Parameter(Mandatory = $true)][int]$RemotePort
  )
  $kubectl = (Get-Command kubectl).Source
  $stdout = Join-Path ([IO.Path]::GetTempPath()) "kravel-$Service-port-forward.out.log"
  $stderr = Join-Path ([IO.Path]::GetTempPath()) "kravel-$Service-port-forward.err.log"
  return Start-Process -FilePath $kubectl `
    -ArgumentList @("-n", $Namespace, "port-forward", "--address=127.0.0.1", "service/$Service", "${LocalPort}:${RemotePort}") `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden `
    -PassThru
}

function Wait-KravelTcpPort {
  param([Parameter(Mandatory = $true)][int]$Port, [int]$TimeoutSeconds = 30)
  Wait-KravelCondition "localhost:$Port" {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
      $task = $client.ConnectAsync("127.0.0.1", $Port)
      return $task.Wait(500) -and $client.Connected
    } catch {
      return $false
    } finally {
      $client.Dispose()
    }
  } $TimeoutSeconds
}

function Get-KravelTimestamp {
  return [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
}
