# Native PowerShell benchmark runner — no Python dependency
# Sends prompts through Anubis proxy, captures responses, saves transcripts
param(
    [Parameter(Mandatory=$true)]
    [string[]]$Tasks,
    [switch]$Scan
)

$apiKey = (Get-Content "$env:USERPROFILE\.anubis\config.yaml" | Select-String "api_key:" | Select-Object -First 1) -replace 'api_key:\s*', '' -replace '"', '' -replace "'", ''
$proxyUrl = "http://127.0.0.1:7878/v1/chat/completions"
$model = "glm-5-turbo"
$scanner = "E:\GitRepos\groundwire\packages\daemon-rs\target\release\scan_transcript.exe"

$langMap = @{
    "rust" = "rust"; "python" = "python"; "ts-" = "typescript"; "go-" = "go"
    "csharp" = "csharp"; "cpp" = "cpp"; "gdscript" = "gdscript"
}

foreach ($task in $Tasks) {
    Write-Host "=== $task ===" -ForegroundColor Cyan
    $specFile = "corpus\hard_tasks\$task\spec.md"
    if (!(Test-Path $specFile)) { Write-Host "  Spec not found: $specFile" -ForegroundColor Red; continue }

    $spec = Get-Content $specFile -Raw
    # Extract prompt: everything after "## Prompt" until next "##" section
    $promptMatch = [regex]::Match($spec, '(?s)## Prompt.*?\n>(.*?)(?=\n##|\Z)')
    $prompt = if ($promptMatch.Success) { $promptMatch.Groups[1].Value.Trim() } else { $spec }
    
    # Determine language from task name
    $lang = ""
    foreach ($k in $langMap.Keys) { if ($task -match $k) { $lang = $langMap[$k]; break } }

    $taskDir = "results\$task"
    New-Item -ItemType Directory -Force -Path $taskDir | Out-Null

    # Build request body
    $body = @{
        model = $model
        messages = @(
            @{ role = "system"; content = "You are a senior software engineer. Produce production-ready code with correct imports, types, and API usage. Output each file as a fenced code block." }
            @{ role = "user"; content = $prompt }
        )
        stream = $false
        temperature = 0.2
        max_tokens = 4096
    } | ConvertTo-Json -Depth 5

    Write-Host "  Sending to $model ($($prompt.Length) chars prompt)..."
    try {
        $resp = Invoke-WebRequest -Uri $proxyUrl -Method POST -Body $body -ContentType "application/json" -Headers @{Authorization="Bearer $($apiKey.Trim())"} -UseBasicParsing -TimeoutSec 180
        [System.IO.File]::WriteAllText("$taskDir\response.json", $resp.Content)
        
        # Extract content via regex-based Python script (handles truncated JSON)
        $taskDirPy = ($taskDir -replace '\\','/')
        python harness/extract_response.py "$taskDir/response.json" "$taskDir/transcript.jsonl" "$taskDir/generated_code.md"
        
        if ($Scan -and (Test-Path "$taskDir\transcript.jsonl")) {
            Write-Host "  Scanning ($lang)..." -ForegroundColor Yellow
            $scanArgs = @("$taskDir\transcript.jsonl", "--project-root", ".")
            if ($lang) { $scanArgs += @("--lang", $lang) }
            & $scanner @scanArgs 2>&1 | Select-String '^\[|^\{|^SUMMARY' | ForEach-Object { Write-Host "  $_" }
        }
        Write-Host "  Done" -ForegroundColor Green
    } catch {
        Write-Host "  FAILED: $($_.ErrorDetails.Message)" -ForegroundColor Red
    }
}
Write-Host "`nComplete: $($Tasks.Count) tasks" -ForegroundColor Green
