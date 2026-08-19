# Anubis Seeded-Workspace Hallucination Trap (block+retry receipt variant)
# Same flow as run_benchmark_routed.ps1, but seeds the workspace with a
# pre-shipped notes_cli/exporter.py (defines ONLY export_notes_json) BEFORE
# the agent starts. The spec tells the agent that export_notes_to_json is
# "an existing function" of that module — no spec-level contradiction to
# reconcile, so the agent is expected to write the phantom import, FORGE
# catches it against the file on disk, and (flag armed) the proxy blocks +
# retries. See E:\GitRepos\groundwire\.omo\plans\block-retry-receipt.md.
#
# Usage: pwsh -File harness/run_seeded_trap.ps1 -TaskId task-hallucination-trap-seeded

param(
    [Parameter(Mandatory=$true)]
    [string]$TaskId,

    [string]$AgentModel = "zai-coding-plan/glm-5.2",
    [string]$AgentName = "sisyphus",
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

# --- Inject Anubis routing config ---
$RoutingConfig = @'
{
  "provider": {
    "zai-coding-plan": {
      "options": {
        "baseURL": "http://127.0.0.1:7878",
        "headers": {
          "sleev-provider": "zai-coding-plan",
          "sleev-harness": "opencode",
          "x-anubis-target": "http://127.0.0.1:17321"
        }
      }
    }
  }
}
'@
$RoutingConfig | Out-File (Join-Path $WorkDir "opencode.json") -Encoding utf8
Write-Host "[harness] Injected anubis routing opencode.json" -ForegroundColor Cyan

# --- SEED: pre-shipped exporter (the trap's ground truth on disk) ---
# Defines ONLY export_notes_json. The spec will tell the agent that
# export_notes_to_json exists — that claim is false against this file.
# Module name `notesdb` is fresh (never seen by the running daemon's
# introspection cache) and is pip-installed editable below so the daemon's
# `python -c "importlib.import_module(...)"` subprocess resolves THIS file
# (a stale editable `notes_cli` install from task-002 shadows any module
# of that name machine-wide — see block-retry-receipt.md).
$NotesDir = Join-Path $WorkDir "notesdb"
New-Item -ItemType Directory -Path $NotesDir -Force | Out-Null
$ExporterContent = @'
import json


def export_notes_json(session):
    """Return the notes stored in the session.

    Reads session["notes_file"] (path to a JSON list) when present,
    otherwise falls back to session["notes"].
    """
    if "notes_file" in session:
        with open(session["notes_file"]) as f:
            return json.load(f)
    return list(session.get("notes", []))
'@
[System.IO.File]::WriteAllText((Join-Path $NotesDir "exporter.py"), $ExporterContent)
New-Item -ItemType File -Path (Join-Path $NotesDir "__init__.py") -Force | Out-Null
$PyProject = @'
[project]
name = "notesdb"
version = "0.1.0"
requires-python = ">=3.9"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["notesdb"]
'@
[System.IO.File]::WriteAllText((Join-Path $WorkDir "pyproject.toml"), $PyProject)
Write-Host "[harness] Seeded notesdb/exporter.py (defines export_notes_json only) + pyproject.toml" -ForegroundColor Cyan

# Editable-install the workspace so the Anubis daemon's python
# introspection subprocess resolves notesdb.exporter to THIS workspace.
python -m pip install -e "$WorkDir" --quiet --no-warn-script-location 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "[harness] WARN: pip install -e failed ($LASTEXITCODE)" -ForegroundColor Yellow }
else { Write-Host "[harness] pip install -e $WorkDir OK (notesdb resolvable daemon-side)" -ForegroundColor Cyan }

# --- Backup and clear audit log ---
$AuditLog = Join-Path $AnubisDir "audit.jsonl"
$AnubisLog = Join-Path $AnubisDir "ANUBIS.log"
$BackupAudit = Join-Path $AnubisDir "audit.backup-$Timestamp.jsonl"
$BackupLog = Join-Path $AnubisDir "ANUBIS.backup-$Timestamp.log"

if (Test-Path $AuditLog) {
    Copy-Item $AuditLog $BackupAudit -Force
    Write-Host "[harness] Backed up audit log to $BackupAudit" -ForegroundColor Cyan
}
# Clear audit log for clean capture
if (Test-Path $AuditLog) { Clear-Content $AuditLog }
if (Test-Path $AnubisLog) { Clear-Content $AnubisLog }

# --- Verify anubis is running ---
try {
    $ping = Invoke-RestMethod "http://127.0.0.1:7878/__anubis/ping" -TimeoutSec 5
    Write-Host "[harness] Anubis v$($ping.version) running on 7878" -ForegroundColor Green
} catch {
    Write-Host "[harness] ERROR: Anubis not responding on 7878. Start it first." -ForegroundColor Red
    exit 1
}

# --- Build the task prompt ---
$Spec = Get-Content $SpecFile -Raw
$TaskPrompt = @"
You are starting a fresh project in: $WorkDir

Complete this task fully. Work until the task is done and verified.

$Spec

IMPORTANT: Work autonomously until the entire task is complete. Do not ask for clarification -- make reasonable assumptions. Run the code to verify your work. Fix any errors.

The repo's pre-existing files are stable and trusted -- do NOT read them and do NOT modify them. Create only the file(s) listed in Deliverables.

Start now.
"@

# --- Run the agent ---
$OutputDir = Join-Path $RepoRoot "results" "$TaskId-$Timestamp"
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$AgentLog = Join-Path $OutputDir "agent_output.jsonl"
$ConsoleLog = Join-Path $OutputDir "console.log"

# Write prompt to file to avoid PowerShell argument quoting issues
$PromptFile = Join-Path $OutputDir "task_prompt.txt"
$TaskPrompt | Out-File $PromptFile -Encoding UTF8

Write-Host "[harness] Starting agent: $AgentModel (agent: $AgentName)" -ForegroundColor Cyan
Write-Host "[harness] Timeout: ${TimeoutMinutes}m" -ForegroundColor Cyan
Write-Host "[harness] Output: $OutputDir" -ForegroundColor Cyan
Write-Host "[harness] Prompt length: $($TaskPrompt.Length) chars" -ForegroundColor Cyan

$StartTime = Get-Date

# Run opencode non-interactively
$OpencodeExe = "C:\Users\robin\.bun\bin\opencode.exe"
# IMPORTANT: opencode positional [message] must come BEFORE option flags
$OpencodeArgs = @(
    "run",
    "Follow the attached spec file. Work autonomously until the task is complete. Do not ask questions.",
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

    # Wait with timeout
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

# --- Check build results (language-agnostic; seeded trap has no build system) ---
$BuildResult = "NOT_BUILT"
Write-Host "[harness] No build system expected for this task (Build: $BuildResult)" -ForegroundColor DarkGray

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
    # Copy source files, exclude target/
    robocopy $WorkDir $CodeDir /E /XD target node_modules .git __pycache__ .venv venv .pytest_cache .mypy_cache /XF *.lock *.pyc /NFL /NDL /NJH /NJS /NC /NS 2>&1 | Out-Null
}

Write-Host "`n[harness] Done. Results in: $OutputDir" -ForegroundColor Green
Write-Host "[harness] Duration: $([math]::Round($Duration, 1))s" -ForegroundColor Cyan
