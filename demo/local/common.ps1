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

function Invoke-KravelNativeWithRetry {
  param(
    [Parameter(Mandatory = $true)][string]$Command,
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [ValidateRange(1, 10)][int]$Attempts = 4
  )
  for ($attempt = 1; $attempt -le $Attempts; $attempt += 1) {
    & $Command @Arguments
    if ($LASTEXITCODE -eq 0) { return }
    if ($attempt -eq $Attempts) {
      throw "Command failed after $Attempts attempts: $Command $($Arguments -join ' ')"
    }
    $delaySeconds = [Math]::Min([Math]::Pow(2, $attempt), 10)
    Write-Warning "Command failed on attempt $attempt of $Attempts. Retrying in $delaySeconds seconds: $Command $($Arguments -join ' ')"
    Start-Sleep -Seconds $delaySeconds
  }
}

function Repair-KravelDockerGpuHelper {
  if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { return $false }
  $targetDirectory = Join-Path $env:USERPROFILE ".docker\bin\inference"
  $target = Join-Path $targetDirectory "com.docker.nv-gpu-info.exe"
  if (Test-Path -LiteralPath $target) {
    $targetSignature = Get-AuthenticodeSignature -LiteralPath $target
    if ($targetSignature.Status -eq "Valid" -and $targetSignature.SignerCertificate.Subject -match "O=Docker Inc") { return $false }
    throw "Docker's installed GPU helper failed signature validation: $target"
  }

  $dockerCommand = Get-Command docker -ErrorAction Stop
  $resourcesDirectory = Split-Path (Split-Path $dockerCommand.Source -Parent) -Parent
  $source = Join-Path $resourcesDirectory "model-runner\bin\com.docker.nv-gpu-info.exe"
  if (-not (Test-Path -LiteralPath $source)) {
    Write-Warning "Docker's GPU helper is missing from both its runtime and installation directories. Reinstall or repair Docker Desktop."
    return $false
  }

  $signature = Get-AuthenticodeSignature -LiteralPath $source
  if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "O=Docker Inc") {
    throw "Refusing to copy an unverified Docker GPU helper from $source"
  }

  New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
  Copy-Item -LiteralPath $source -Destination $target -Force
  Write-Warning "Applied the Docker Desktop GPU-helper provisioning workaround using Docker's verified bundled executable."
  return $true
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

function Select-KravelDesktopModelRunner {
  $contextName = "kravel-desktop"
  $endpoint = "http://127.0.0.1:12434"
  $desktopEndpointAvailable = $false
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri "$endpoint/engines/v1/models" -TimeoutSec 5
    $desktopEndpointAvailable = $response.StatusCode -eq 200
  } catch {
    $desktopEndpointAvailable = $false
  }

  if ($desktopEndpointAvailable) {
    $inspection = & docker model context inspect $contextName 2>$null
    if ($LASTEXITCODE -ne 0) {
      Invoke-KravelNative "docker" @(
        "model", "context", "create", $contextName,
        "--host", $endpoint,
        "--description", "Kravel localhost-only Docker Desktop Model Runner"
      )
    } else {
      $context = ($inspection -join [Environment]::NewLine) | ConvertFrom-Json
      if ($context[0].host -ne $endpoint) {
        throw "Docker model context '$contextName' already exists with a different host. Inspect it with: docker model context inspect $contextName"
      }
    }
    Invoke-KravelNative "docker" @("model", "context", "use", $contextName)
  }

  $gpuHelperRepaired = Repair-KravelDockerGpuHelper
  if ($gpuHelperRepaired) {
    throw "Docker's signed GPU helper was repaired. Restart Docker Desktop once with 'docker desktop restart', wait for it to report running, and then run prepare.cmd again."
  }
  try {
    Invoke-KravelNative "docker" @("model", "status")
  } catch {
    throw "Docker Model Runner is not reachable. In Docker Desktop, enable Model Runner, GPU-backed inference, and localhost TCP support on port 12434, then retry. $($_.Exception.Message)"
  }
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
