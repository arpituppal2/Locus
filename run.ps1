param(
  [switch]$AllowModels,
  [double]$MaxRamGb,
  [switch]$NoAutoSelectModels,
  [switch]$AllowExternalAi,
  [switch]$AllowCloudWorkers,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Goal
)

$ErrorActionPreference = "Stop"
$AidDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Resolve-Python {
  if (Get-Command python -ErrorAction SilentlyContinue) {
    return @{ Command = "python"; Args = @() }
  }
  if (Get-Command py -ErrorAction SilentlyContinue) {
    return @{ Command = "py"; Args = @("-3") }
  }
  $bootstrap = Join-Path $AidDir "scripts/bootstrap_python_windows.ps1"
  & powershell -NoProfile -ExecutionPolicy Bypass -File $bootstrap
  if (Get-Command python -ErrorAction SilentlyContinue) {
    return @{ Command = "python"; Args = @() }
  }
  if (Get-Command py -ErrorAction SilentlyContinue) {
    return @{ Command = "py"; Args = @("-3") }
  }
  throw "Python 3.11 or newer is required, and automatic installation did not make it available in this shell."
}

function Use-Default {
  param([string]$Value, [string]$Default)
  if ([string]::IsNullOrWhiteSpace($Value)) {
    return $Default
  }
  return $Value
}

$Python = Resolve-Python
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$AidDir;$env:PYTHONPATH" } else { $AidDir }
$env:LOCAL_COMPUTER_ALLOW_MODELS = if ($AllowModels) { "1" } else { Use-Default $env:LOCAL_COMPUTER_ALLOW_MODELS "0" }
$env:LOCAL_COMPUTER_ALLOW_EXTERNAL_AI = if ($AllowExternalAi) { "1" } else { Use-Default $env:LOCAL_COMPUTER_ALLOW_EXTERNAL_AI "0" }
$env:LOCAL_COMPUTER_ALLOW_CLOUD_WORKERS = if ($AllowCloudWorkers) { "1" } else { Use-Default $env:LOCAL_COMPUTER_ALLOW_CLOUD_WORKERS "0" }
$env:LOCAL_COMPUTER_SKIP_MODEL_VALIDATE = Use-Default $env:LOCAL_COMPUTER_SKIP_MODEL_VALIDATE "1"
$env:LOCAL_COMPUTER_MAX_GPU_PERCENT = Use-Default $env:LOCAL_COMPUTER_MAX_GPU_PERCENT "95"
$env:TOKENIZERS_PARALLELISM = "false"
if ($PSBoundParameters.ContainsKey("MaxRamGb")) {
  $env:LOCAL_COMPUTER_MAX_RAM_GB = [string]$MaxRamGb
}
if ($NoAutoSelectModels) {
  $env:LOCAL_COMPUTER_AUTO_SELECT_MODELS = "0"
}

try {
  $budgetJson = & $Python.Command @($Python.Args) (Join-Path $AidDir "scripts/resource_policy.py") --json 2>$null
  if ($LASTEXITCODE -eq 0 -and $budgetJson) {
    $budget = $budgetJson | ConvertFrom-Json
    foreach ($pair in $budget.env.PSObject.Properties) {
      [Environment]::SetEnvironmentVariable($pair.Name, [string]$pair.Value, "Process")
    }
  }
} catch {
  Write-Host "[setup] Resource policy will be checked after dependencies are ready."
}

if ($env:LOCAL_COMPUTER_ALLOW_MODELS -eq "1") {
  $env:LOCAL_COMPUTER_SKIP_MODEL_VALIDATE = "0"
  if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw "Install Ollama from https://ollama.ai before using --AllowModels."
  }
  & ollama list *> $null
  if ($LASTEXITCODE -ne 0) {
    throw "Ollama is not running. Start it with: ollama serve"
  }
  Write-Host "[models] Hardware-aware recommendation:"
  if ($PSBoundParameters.ContainsKey("MaxRamGb")) {
    & $Python.Command @($Python.Args) (Join-Path $AidDir "scripts/model_selector.py") --max-ram-gb $MaxRamGb
  } else {
    & $Python.Command @($Python.Args) (Join-Path $AidDir "scripts/model_selector.py")
  }
} else {
  $env:LOCAL_COMPUTER_ALLOW_MODELS = "0"
}

$portBusy = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($portBusy) {
  throw "Port 8765 is already in use. Stop the existing Locus server and try again."
}

& $Python.Command @($Python.Args) (Join-Path $AidDir "scripts/setup_manager.py") --bootstrap
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

$VenvPython = Join-Path $AidDir ".venv\Scripts\python.exe"
Set-Location $AidDir

if ($Goal -and $Goal.Count -gt 0) {
  if ($env:LOCAL_COMPUTER_ALLOW_MODELS -eq "1") {
    Write-Host "[run] Running one-shot research query with local models enabled"
    & $VenvPython "scripts/orchestrator.py" @Goal
  } else {
    Write-Host "[run] Running model-free workspace query"
    & $VenvPython "scripts/workspace_agent.py" @Goal
  }
} else {
  Write-Host "[run] Starting dashboard server at http://127.0.0.1:8765"
  & $VenvPython "scripts/ui_server.py" --host 127.0.0.1 --port 8765
}
