# Anubis E2E Benchmark Harness (ollama-routed variant)
# Same as run_benchmark_routed.ps1, but routes the LOCAL ollama provider
# through the Anubis proxy (7878) with x-anubis-target pointing at ollama
# (11434). Purpose: weak-model e2e — qwen2.5-coder:7b class coders
# hallucinate at the 3-19% rate the external studies report, letting us
# measure scanner catch rate on REAL (non-synthetic) hallucinations.
#
# Usage: pwsh -File harness/run_benchmark_ollama.ps1 -TaskId task-002-python-notes-cli

param(
    [Parameter(Mandatory=$true)]
    [string]$TaskId,

    [string]$AgentModel = "ollama/qwen2.5-coder:7b-opencode",
    [string]$AgentName = "build",
    [int]$TimeoutMinutes = 30,
    [string]$WorkDir = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$AnubisDir = Join-Path $env:USERPROFILE ".anubis"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

# --- Validate task exists ---
$TaskDir = Join-Path $RepoRoot "tasks" $TaskId
$SpecFile = Join-Path $TaskDir "spec.md"
if (-not (Test-Path $SpecFile)) {
    Write-Host "ERROR: Task spec not found: $SpecFile" -ForegroundColor Red
    exit 1
}

# --- Create workspace ---
if (-not $WorkDir) {
    $WorkDir = Join-Path $env:TEMP "anubis-bench-$TaskId-$Timestamp"
}
if (Test-Path $WorkDir) { Remove-Item $WorkDir -Recurse -Force }
New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null
Write-Host "[harness] Workspace: $WorkDir" -ForegroundColor Cyan

# --- Inject Anubis routing config (ollama through proxy) ---
$RoutingConfig = @'
{
  "provider": {
    "ollama": {
      "options": {
        "baseURL": "http://127.0.0.1:7878/v1",
        "headers": {
          "sleev-harness": "opencode",
          "x-anubis-target": "http://127.0.0.1:17321",
          "sleev-provider": "zai-coding-plan"
        }
      }
    }
  }
}
'@
$RoutingConfig | Out-File (Join-Path $WorkDir "opencode.json") -Encoding utf8
Write-Host "[harness] Injected ollama->anubis routing opencode.json" -ForegroundColor Cyan

# --- Backup and clear audit log ---
$AuditLog = Join-Path $AnubisDir "audit.jsonl"
$AnubisLog = Join-Path $AnubisDir "ANUBIS.log"
$BackupAudit = Join-Path $AnubisDir "audit.backup-$Timestamp.jsonl"
$BackupLog = Join-Path $AnubisDir "ANUBIS.backup-$Timestamp.log"

if (Test-Path $AuditLog) {
    Copy-Item $AuditLog $BackupAudit -Force
    Write-Host "[harness] Backed up audit log to $BackupAudit" -ForegroundColor Cyan
}
if (Test-Path $AuditLog) { Clear-Content $AuditLog }
if (Test-Path $AnubisLog) { Clear-Content $AnubisLog }

# --- Verify anubis + ollama are running ---
try {
    $ping = Invoke-RestMethod "http://127.0.0.1:7878/__anubis/ping" -TimeoutSec 5
    Write-Host "[harness] Anubis v$($ping.version) running on 7878" -ForegroundColor Green
} catch {
    Write-Host "[harness] ERROR: Anubis not responding on 7878. Start it first." -ForegroundColor Red
    exit 1
}
try {
    $null = Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
    Write-Host "[harness] Ollama running on 11434" -ForegroundColor Green
} catch {
    Write-Host "[harness] ERROR: Ollama not responding on 11434. Start it first." -ForegroundColor Red
    exit 1
}

# --- Build the task prompt ---
$Spec = Get-Content $SpecFile -Raw
$TaskPrompt = @"
You are starting a fresh project in: $WorkDir

Complete this task fully. Create all files from scratch. Work until the build and tests both pass.

$Spec

IMPORTANT: Work autonomously until the entire task is complete. Do not ask for clarification -- make reasonable assumptions. Run the build and test commands to verify your work. Fix any errors. The task is done when:
1. The project builds without errors
2. All tests pass
3. All required features are implemented

Start by creating the project, then implement each feature.
"@

# --- Run the agent ---
$OutputDir = Join-Path $RepoRoot "results" "$TaskId-$Timestamp"
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$AgentLog = Join-Path $OutputDir "agent_output.jsonl"
$ConsoleLog = Join-Path $OutputDir "console.log"

$PromptFile = Join-Path $OutputDir "task_prompt.txt"
$TaskPrompt | Out-File $PromptFile -Encoding UTF8

Write-Host "[harness] Starting agent: $AgentModel (agent: $AgentName)" -ForegroundColor Cyan
Write-Host "[harness] Timeout: ${TimeoutMinutes}m" -ForegroundColor Cyan
Write-Host "[harness] Output: $OutputDir" -ForegroundColor Cyan
Write-Host "[harness] Prompt length: $($TaskPrompt.Length) chars" -ForegroundColor Cyan

$StartTime = Get-Date

$OpencodeExe = "C:\Users\robin\.bun\bin\opencode.exe"
# Minimal XDG config: strips oh-my-opencode plugins/skills/team tools so a 7B
# model sees only built-in tools (big toolsets make weak models emit JSON
# tool-calls as text and quit after one step).
$env:XDG_CONFIG_HOME = "$env:TEMP\opencode-minimal"
$env:XDG_DATA_HOME = "$env:TEMP\opencode-minimal\data"
# IMPORTANT: opencode positional [message] must come BEFORE option flags
$OpencodeArgs = @(
    "run",
    "Follow the attached spec file. Work autonomously until the build and tests pass. Do not ask questions.",
    "--auto",
    "--format", "json",
    "-m", $AgentModel,
    "--agent", $AgentName,
    "--dir", $WorkDir,
    "--print-logs",
    "--log-level", "DEBUG",
    "-f", $PromptFile
)

$ArgsString = $OpencodeArgs | ForEach-Object {
    if ($_ -match '\s') { "`"$_`"" } else { $_ }
} | Join-String -Separator ' '

Write-Host "[harness] Running: opencode $ArgsString" -ForegroundColor DarkGray

try {
    $process = Start-Process -FilePath $OpencodeExe `
        -ArgumentList $ArgsString `
        -RedirectStandardOutput $AgentLog `
        -RedirectStandardError $ConsoleLog `
        -NoNewWindow `
        -PassThru

    $waited = $process.WaitForExit($TimeoutMinutes * 60 * 1000)
    if (-not $waited) {
        Write-Host "[harness] TIMEOUT: Agent did not finish in ${TimeoutMinutes}m. Killing." -ForegroundColor Yellow
        $process | Stop-Process -Force
    }
} catch {
    Write-Host "[harness] Agent process error: $_" -ForegroundColor Red
}

$EndTime = Get-Date
$Duration = ($EndTime - $StartTime).TotalSeconds

Write-Host "[harness] Agent finished in $([math]::Round($Duration, 1))s" -ForegroundColor Cyan

# --- Copy audit log + anubis log ---
Copy-Item $AuditLog (Join-Path $OutputDir "audit.jsonl") -ErrorAction SilentlyContinue
Copy-Item $AnubisLog (Join-Path $OutputDir "anubis.log") -ErrorAction SilentlyContinue

# --- Check build results (language-agnostic) ---
$BuildResult = "NOT_BUILT"
Push-Location $WorkDir
try {
    if (Test-Path (Join-Path $WorkDir "Cargo.toml")) {
        Write-Host "[harness] Running cargo build..." -ForegroundColor Cyan
        $buildOutput = cargo build --release 2>&1
        $buildOutput | Out-File (Join-Path $OutputDir "build.log")
        $BuildResult = if ($LASTEXITCODE -eq 0) { "BUILD_OK" } else { "BUILD_FAIL" }
        Write-Host "[harness] Build: $BuildResult" -ForegroundColor $(if ($LASTEXITCODE -eq 0) {'Green'} else {'Red'})

        Write-Host "[harness] Running cargo test..." -ForegroundColor Cyan
        $testOutput = cargo test 2>&1
        $testOutput | Out-File (Join-Path $OutputDir "test.log")
        $BuildResult += if ($LASTEXITCODE -eq 0) { "_TEST_OK" } else { "_TEST_FAIL" }
        Write-Host "[harness] Tests: $(if ($LASTEXITCODE -eq 0) {'OK'} else {'FAILED'})" -ForegroundColor $(if ($LASTEXITCODE -eq 0) {'Green'} else {'Red'})

    } elseif (Test-Path (Join-Path $WorkDir "go.mod")) {
        Write-Host "[harness] Running go build..." -ForegroundColor Cyan
        $buildOutput = go build ./... 2>&1
        $buildOutput | Out-File (Join-Path $OutputDir "build.log")
        $BuildResult = if ($LASTEXITCODE -eq 0) { "BUILD_OK" } else { "BUILD_FAIL" }
        Write-Host "[harness] Build: $BuildResult" -ForegroundColor $(if ($LASTEXITCODE -eq 0) {'Green'} else {'Red'})

        Write-Host "[harness] Running go test..." -ForegroundColor Cyan
        $testOutput = go test ./... 2>&1
        $testOutput | Out-File (Join-Path $OutputDir "test.log")
        $BuildResult += if ($LASTEXITCODE -eq 0) { "_TEST_OK" } else { "_TEST_FAIL" }
        Write-Host "[harness] Tests: $(if ($LASTEXITCODE -eq 0) {'OK'} else {'FAILED'})" -ForegroundColor $(if ($LASTEXITCODE -eq 0) {'Green'} else {'Red'})

    } elseif ((Test-Path (Join-Path $WorkDir "pyproject.toml")) -or (Test-Path (Join-Path $WorkDir "setup.py"))) {
        Write-Host "[harness] Running pip install -e ." -ForegroundColor Cyan
        $buildOutput = pip install -e . 2>&1
        $buildOutput | Out-File (Join-Path $OutputDir "build.log")
        $BuildResult = if ($LASTEXITCODE -eq 0) { "BUILD_OK" } else { "BUILD_FAIL" }
        Write-Host "[harness] Install: $BuildResult" -ForegroundColor $(if ($LASTEXITCODE -eq 0) {'Green'} else {'Red'})

        Write-Host "[harness] Running pytest..." -ForegroundColor Cyan
        $testOutput = python -m pytest -v 2>&1
        $testOutput | Out-File (Join-Path $OutputDir "test.log")
        $BuildResult += if ($LASTEXITCODE -eq 0) { "_TEST_OK" } else { "_TEST_FAIL" }
        Write-Host "[harness] Tests: $(if ($LASTEXITCODE -eq 0) {'OK'} else {'FAILED'})" -ForegroundColor $(if ($LASTEXITCODE -eq 0) {'Green'} else {'Red'})

    } elseif (Test-Path (Join-Path $WorkDir "package.json")) {
        Write-Host "[harness] Running npm install..." -ForegroundColor Cyan
        $buildOutput = npm install 2>&1
        $buildOutput | Out-File (Join-Path $OutputDir "build.log")
        $BuildResult = if ($LASTEXITCODE -eq 0) { "BUILD_OK" } else { "BUILD_FAIL" }

        Write-Host "[harness] Running npm test..." -ForegroundColor Cyan
        $testOutput = npm test 2>&1
        $testOutput | Out-File (Join-Path $OutputDir "test.log")
        $BuildResult += if ($LASTEXITCODE -eq 0) { "_TEST_OK" } else { "_TEST_FAIL" }

    } else {
        Write-Host "[harness] No known project file found (Cargo.toml/pyproject.toml/package.json)" -ForegroundColor Red
    }
} finally {
    Pop-Location
}

# --- Save metadata ---
$Metadata = @{
    task_id = $TaskId
    timestamp = $Timestamp
    agent_model = $AgentModel
    agent_name = $AgentName
    work_dir = $WorkDir
    output_dir = $OutputDir
    duration_seconds = [math]::Round($Duration, 1)
    build_result = $BuildResult
    start_time = $StartTime.ToString("o")
    end_time = $EndTime.ToString("o")
}
$Metadata | ConvertTo-Json | Out-File (Join-Path $OutputDir "metadata.json")

# --- Copy workspace files (for code review) ---
$CodeDir = Join-Path $OutputDir "workspace"
if (Test-Path $WorkDir) {
    robocopy $WorkDir $CodeDir /E /XD target node_modules .git __pycache__ .venv venv .pytest_cache .mypy_cache /XF *.lock *.pyc /NFL /NDL /NJH /NJS /NC /NS 2>&1 | Out-Null
}

Write-Host "`n[harness] Done. Results in: $OutputDir" -ForegroundColor Green
Write-Host "[harness] Build: $BuildResult" -ForegroundColor Cyan
Write-Host "[harness] Duration: $([math]::Round($Duration, 1))s" -ForegroundColor Cyan

# --- Run evaluation ---
$EvalScript = Join-Path $RepoRoot "evaluation" "evaluate.ps1"
if (Test-Path $EvalScript) {
    Write-Host "`n[harness] Running evaluation..." -ForegroundColor Cyan
    & powershell -File $EvalScript -OutputDir $OutputDir
}
