# Wrapper: sets env vars + runs Python benchmark in same process
# Usage: .\run_benchmark_parallel.ps1 -Tasks "task-01-rust-sqlx","task-02-python-django" [-Scan]
param(
    [Parameter(Mandatory=$true)]
    [string[]]$Tasks,
    [switch]$Scan
)

$apiKey = (Get-Content "$env:USERPROFILE\.anubis\config.yaml" | Select-String "api_key:" | Select-Object -First 1) -replace 'api_key:\s*', '' -replace '"', '' -replace "'", ''
$env:BENCHMARK_API_KEY = $apiKey.Trim()
$env:OLLAMA_URL = "http://127.0.0.1:7878/v1/chat/completions"
$env:OLLAMA_MODEL = "glm-5-turbo"
$env:OLLAMA_TIMEOUT = "180"
$env:OLLAMA_MAX_TOKENS = "4096"
$env:RUST_LOG = "warn"
$env:DELULU_FORGE_ONLY = "1"

$scanner = "E:\GitRepos\groundwire\packages\daemon-rs\target\release\scan_transcript.exe"

foreach ($task in $Tasks) {
    Write-Host "=== $task ===" -ForegroundColor Cyan
    $taskDir = "results\$task"
    New-Item -ItemType Directory -Force -Path $taskDir | Out-Null

    # Run benchmark
    python harness/run_hard_benchmark.py --task $task 2>&1 | ForEach-Object { Write-Host $_ }

    if ($Scan -and (Test-Path "$taskDir\transcript.jsonl")) {
        Write-Host "--- Scanning transcript ---" -ForegroundColor Yellow
        & $scanner "$taskDir\transcript.jsonl" --lang "$(if($task -match 'rust'){'rust'}elseif($task -match 'python'){'python'}elseif($task -match 'ts-'){'typescript'}elseif($task -match 'go-'){'go'}elseif($task -match 'csharp'){'csharp'}elseif($task -match 'cpp'){'cpp'}else{''})" --project-root "." 2>&1 | ForEach-Object { Write-Host $_ }
    }
    Write-Host ""
}

Write-Host "Done: $($Tasks.Count) tasks" -ForegroundColor Green
