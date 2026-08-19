<#
.SYNOPSIS
  Run a held-out MultiPL-E task through the Anubis-proxied agent.

.DESCRIPTION
  Parallel to run_benchmark.ps1 but tailored for held-out tasks:
  - Pre-places workspace_template/* into the agent's workdir
  - Uses a "complete the function" prompt instead of "build from spec"
  - Runs language-specific test commands (cargo test / pytest / vitest / go test)
  - Captures audit.jsonl + agent_output.jsonl + warnings for analysis

  Held-out corpus policy (see HELD_OUT_README.md):
  DO NOT tune scanner weights, thresholds, or skip-lists against
  results from this script. The corpus exists to measure generalization.

.PARAMETER TaskId
  Held-out task directory name (e.g. held-out-multipl-rs-humaneval_0_has_close_elements).

.PARAMETER AgentModel
  Model ID passed to opencode. Default: zai-coding-plan/glm-4.7 (full
  model, NOT Flash -- held-out evaluation wants the strongest reasoning).

.PARAMETER AgentName
  Opencode agent profile. Default: sisyphus.

.PARAMETER TimeoutMinutes
  Hard cap on agent runtime. Default: 15 (function-completion is much
  shorter than full project builds).

.EXAMPLE
  powershell -File harness\run_held_out.ps1 -TaskId held-out-multipl-py-humaneval-0
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TaskId,

    [string]$AgentModel = 'zai-coding-plan/glm-4.7',
    [string]$AgentName = 'sisyphus',
    [int]$TimeoutMinutes = 15,
    [switch]$BypassAnubis
)

$ErrorActionPreference = 'Stop'
$RepoRoot   = Split-Path -Parent $PSScriptRoot
$TaskDir    = Join-Path $RepoRoot "tasks\$TaskId"
$TemplateDir = Join-Path $TaskDir 'workspace_template'
$SpecPath   = Join-Path $TaskDir 'spec.md'
$ResultsRoot = Join-Path $RepoRoot 'results'
$Timestamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
$RunPrefix  = if ($BypassAnubis) { 'no-anubis-' } else { '' }
$OutputDir  = Join-Path $ResultsRoot "$RunPrefix$TaskId-$Timestamp"

# When bypassing anubis, point opencode at a side-by-side config that
# routes zai-coding-plan directly to api.z.ai (no proxy interception,
# no @groundwire/opencode plugin feedback to the agent). Setup script:
# harness/setup_no_anubis_config.ps1 (or whichever produced the config).
#
# S3 fix: $env:XDG mutation must not leak past script exit. Capture the
# caller's prior value, then wrap the rest of the script in try/finally
# so restoration runs even on error or early Write-Error.
$PrevXdg = $env:XDG_CONFIG_HOME
try {
    if ($BypassAnubis) {
        $env:XDG_CONFIG_HOME = 'C:\Users\robin\AppData\Local\Temp\opencode-no-anubis'
        Write-Host "[bypass] XDG_CONFIG_HOME = $env:XDG_CONFIG_HOME"
        Write-Host "[bypass] agent will reach z.ai directly, no scanner interception"
    }
$WorkDir    = Join-Path $env:TEMP "anubis-heldout-$TaskId-$Timestamp"
$AnubisHome = Join-Path $env:USERPROFILE '.anubis'
$AuditLog   = Join-Path $AnubisHome 'audit.jsonl'
$AnubisLog  = Join-Path $AnubisHome 'ANUBIS.log'
$OpencodeExe = 'C:\Users\robin\.bun\bin\opencode.exe'

if (-not (Test-Path $TaskDir)) { throw "Task directory not found: $TaskDir" }
if (-not (Test-Path $SpecPath)) { throw "spec.md missing in $TaskDir" }
if (-not (Test-Path $TemplateDir)) { throw "workspace_template/ missing in $TaskDir" }

# Sanity: daemon must be running.
try {
    $ping = Invoke-RestMethod -Uri 'http://127.0.0.1:7878/__anubis/ping' -TimeoutSec 5
    Write-Host "anubis daemon: $($ping.version)"
} catch {
    throw "Anubis daemon not reachable at 127.0.0.1:7878. Start it before running."
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null

# Copy workspace_template into WorkDir.
Write-Host "copying workspace_template -> $WorkDir"
Copy-Item -Path (Join-Path $TemplateDir '*') -Destination $WorkDir -Recurse -Force

# Back up + clear audit log so this run's scans are isolated.
if (Test-Path $AuditLog) {
    $Backup = Join-Path $OutputDir 'audit-prev.jsonl'
    Copy-Item $AuditLog $Backup -Force
    Remove-Item $AuditLog -Force
}

# Build the prompt. Held-out tasks ask the agent to fill in a function
# body; tests are pre-placed and must NOT be modified.
$SpecText = Get-Content $SpecPath -Raw -Encoding UTF8
$Prompt = @"
You are completing a held-out function-implementation task sourced from
a public LLM coding benchmark (MultiPL-E / HumanEval). The workspace has
been pre-populated with:

  - A stub solution file (the function signature is present, body is empty)
  - A test file that exercises the function
  - Project scaffolding (Cargo.toml / package.json / pyproject.toml / go.mod)

Your job:
  1. Read the spec below.
  2. Implement ONLY the function body in the solution file.
  3. DO NOT modify the test file or project scaffolding.
  4. Run the tests. If they fail, fix your implementation until they pass.

Spec follows.

---- BEGIN SPEC ----
$SpecText
---- END SPEC ----

Work autonomously. Do not ask questions. Stop when tests pass.
"@

$PromptFile = Join-Path $OutputDir 'task_prompt.txt'
Set-Content -Path $PromptFile -Value $Prompt -Encoding UTF8

# Spawn opencode with the prompt file.
# NOTE on PowerShell quoting: per librarian research, single-string
# -ArgumentList gets re-tokenized badly when values contain spaces.
# Pass each arg as a separate array element to preserve boundaries.
$Args = @(
    'run',
    'Complete the held-out function implementation per the attached spec.',
    '--auto',
    '--format', 'json',
    '-m', $AgentModel,
    '--agent', $AgentName,
    '--dir', $WorkDir,
    '--print-logs',
    '--log-level', 'DEBUG',
    '-f', $PromptFile
)

$AgentOut = Join-Path $OutputDir 'agent_output.jsonl'
$ConsoleLog = Join-Path $OutputDir 'console.log'

Write-Host "spawning opencode (timeout ${TimeoutMinutes}m)"
$proc = Start-Process -FilePath $OpencodeExe `
    -ArgumentList $Args `
    -RedirectStandardOutput $AgentOut `
    -RedirectStandardError  $ConsoleLog `
    -NoNewWindow -PassThru

if (-not $proc) { throw "failed to spawn opencode" }

$exited = $proc.WaitForExit($TimeoutMinutes * 60 * 1000)
if (-not $exited) {
    Write-Warning "agent timed out after ${TimeoutMinutes}m -- killing"
    try { $proc.Kill() } catch {}
    $timeoutHit = $true
} else {
    $timeoutHit = $false
}

# Snapshot audit + ANUBIS logs (defer until process fully releases handles).
Start-Sleep -Seconds 1
if (Test-Path $AuditLog) { Copy-Item $AuditLog (Join-Path $OutputDir 'audit.jsonl') -Force }
if (Test-Path $AnubisLog) { Copy-Item $AnubisLog (Join-Path $OutputDir 'ANUBIS.log') -Force }

# Determine which test command to run based on language marker in TaskId.
$BuildLog = Join-Path $OutputDir 'build.log'
$TestLog  = Join-Path $OutputDir 'test.log'

function Invoke-Checked {
    param([string]$Label, [scriptblock]$Action, [string]$LogPath)
    Write-Host "$Label ..."
    try {
        & $Action *>&1 | Tee-Object -FilePath $LogPath | Out-Null
        $code = $LASTEXITCODE
    } catch {
        Write-Warning "$Label threw: $_"
        $code = -1
    }
    Write-Host "$Label exit=$code"
    return $code
}

$testExit = -1
$buildExit = -1
if ($TaskId -match '-rs-') {
    $buildExit = Invoke-Checked 'cargo build' { & cargo build --manifest-path (Join-Path $WorkDir 'Cargo.toml') } $BuildLog
    $testExit  = Invoke-Checked 'cargo test'  { & cargo test  --manifest-path (Join-Path $WorkDir 'Cargo.toml') } $TestLog
} elseif ($TaskId -match '-py-') {
    $testExit  = Invoke-Checked 'pytest' { & python -m pytest (Join-Path $WorkDir 'tests') -v } $TestLog
} elseif ($TaskId -match '-ts-') {
    Push-Location $WorkDir
    try {
        if (-not (Test-Path (Join-Path $WorkDir 'node_modules'))) {
            Invoke-Checked 'npm install' { & npm install --silent } (Join-Path $OutputDir 'npm-install.log') | Out-Null
        }
        $testExit = Invoke-Checked 'vitest run' { & npx vitest run } $TestLog
    } finally { Pop-Location }
} elseif ($TaskId -match '-go-') {
    $testExit = Invoke-Checked 'go test' { & go test (Join-Path $WorkDir '...') } $TestLog
} else {
    Write-Warning "unknown language for TaskId=$TaskId; skipping test verification"
}

# Metadata.
$Meta = @{
    task_id        = $TaskId
    timestamp      = $Timestamp
    agent_model    = $AgentModel
    agent_name     = $AgentName
    timeout_minutes = $TimeoutMinutes
    timeout_hit    = $timeoutHit
    build_exit     = $buildExit
    test_exit      = $testExit
    workdir        = $WorkDir
    output_dir     = $OutputDir
    held_out       = $true
    bypass_anubis  = [bool]$BypassAnubis
    source         = 'MultiPL-E / HumanEval (public LLM coding benchmark)'
    freeze_notice  = 'DO NOT tune scanner weights/thresholds against this corpus. See HELD_OUT_README.md.'
}
$Meta | ConvertTo-Json | Set-Content -Path (Join-Path $OutputDir 'metadata.json') -Encoding UTF8

# Copy workspace (excluding heavy build artifacts) for forensics.
$Exclude = @('target', 'node_modules', '__pycache__', '.pytest_cache', 'dist', 'build')
$RoboArgs = @($WorkDir, (Join-Path $OutputDir 'workspace'), '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
foreach ($e in $Exclude) { $RoboArgs += "/XD"; $RoboArgs += (Join-Path $WorkDir $e) }
Start-Process -FilePath 'robocopy.exe' -ArgumentList $RoboArgs -NoNewWindow -Wait | Out-Null

# Defer to evaluate.ps1 for warning aggregation.
$Evaluate = Join-Path $RepoRoot 'evaluation\evaluate.ps1'
if (Test-Path $Evaluate) {
    Write-Host "running evaluation\evaluate.ps1"
    try {
        & $Evaluate -OutputDir $OutputDir
    } catch {
        Write-Warning "evaluate.ps1 threw: $_"
    }
}

Write-Host ""
Write-Host "=== held-out run complete ==="
Write-Host "  task        : $TaskId"
Write-Host "  output dir  : $OutputDir"
Write-Host "  build exit  : $buildExit"
Write-Host "  test exit   : $testExit"
Write-Host "  timeout hit : $timeoutHit"
Write-Host "  freeze      : DO NOT tune weights against this corpus (see HELD_OUT_README.md)"
} finally {
    # S3 fix: restore caller's XDG_CONFIG_HOME so subsequent commands in the
    # same PowerShell session aren't accidentally routed through the bypass
    # config. Handles both "had a value" and "didn't have one" cases.
    if ($null -eq $PrevXdg) {
        Remove-Item env:XDG_CONFIG_HOME -ErrorAction SilentlyContinue
    } else {
        $env:XDG_CONFIG_HOME = $PrevXdg
    }
}
