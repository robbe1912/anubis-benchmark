# harness/setup_side_by_side_configs.ps1
#
# Sets up the two parallel opencode config directories the side-by-side
# harness needs:
#   - $env:TEMP\opencode-no-anubis\opencode\opencode.json  (z.ai DIRECT)
#   - $env:TEMP\opencode-with-anubis\opencode\opencode.json (z.ai -> 127.0.0.1:7878)
#
# The two configs are otherwise identical (same ollama models, same MCPs,
# same agents). Only the z.ai baseURL differs, so the side-by-side is
# controlled: the ONLY variable is whether the Anubis scanner intercepts
# z.ai traffic.
#
# Usage:
#   pwsh harness/setup_side_by_side_configs.ps1
#
# Idempotent: safe to re-run.

[CmdletBinding()]
param(
    # Source of the no-anubis config. If absent, the script will attempt to
    # clone the user's regular config from $HOME\.config\opencode\opencode.json
    # and rewrite the z.ai baseURL.
    [string]$SourceConfig = "",

    # Anubis proxy URL the with-mode config will route z.ai through.
    [string]$AnubisBaseUrl = "http://127.0.0.1:7878",

    # Direct z.ai API URL the without-mode config will use (no proxy).
    [string]$DirectZaiBaseUrl = "https://api.z.ai/api/coding/paas/v4"
)

$ErrorActionPreference = "Stop"

$NoAnubisDir   = Join-Path $env:TEMP "opencode-no-anubis\opencode"
$WithAnubisDir = Join-Path $env:TEMP "opencode-with-anubis\opencode"
$NoAnubisJson  = Join-Path $NoAnubisDir "opencode.json"
$WithAnubisJson = Join-Path $WithAnubisDir "opencode.json"

# 1. If no-anubis config already exists, treat it as source of truth.
if (-not $SourceConfig -and (Test-Path $NoAnubisJson)) {
    $SourceConfig = $NoAnubisJson
    Write-Host "[setup] using existing no-anubis config as source: $SourceConfig"
}

# 2. Fall back to user's regular config if present.
if (-not $SourceConfig) {
    $candidates = @(
        Join-Path $HOME ".config\opencode\opencode.json"
        Join-Path $HOME ".config\opencode\opencode.jsonc"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $SourceConfig = $c
            Write-Host "[setup] using regular opencode config as source: $c"
            break
        }
    }
}

if (-not $SourceConfig) {
    Write-Error "No source opencode config found. Place one at $NoAnubisJson manually and re-run."
    exit 2
}

# 3. Copy source -> both target dirs.
foreach ($Dir in @($NoAnubisDir, $WithAnubisDir)) {
    if (-not (Test-Path $Dir)) {
        New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    }
}

# Source config might be .jsonc with comments; opencode's jsonc parser
# handles them but PowerShell's ConvertFrom-Json does not. Try strict JSON
# first; only fall back to comment-stripping if that fails.
function Read-OpencodeConfig {
    param([string]$Path)
    $raw = Get-Content $Path -Raw
    try {
        return $raw | ConvertFrom-Json
    } catch {
        # Strip // line comments and /* */ block comments. NOTE: this is a
        # naive stripper that does NOT track string literals, so source files
        # that contain `//` inside string values (e.g. URLs) will be
        # corrupted. Prefer .json (strict) sources when possible.
        $stripped = $raw -replace '/\*[\s\S]*?\*/', '' -replace '//.*', ''
        return $stripped | ConvertFrom-Json
    }
}

$cfg = Read-OpencodeConfig $SourceConfig

# 3.5 Scrub secrets from the config copy (S5 secret-leak fix).
# The source config may contain Authorization headers for MCP servers
# (GITHUB_TOKEN, SLACK_TOKEN, NOTION_TOKEN, ...) and apiKey fields for
# other providers. Those propagate to the temp XDG dir, which the spawned
# agent can read. Scrub everything that isn't the z.ai provider we control.
function Scrub-Secrets {
    param($Node)

    if ($null -eq $Node) { return }

    # Recurse into PSCustomObject properties.
    if ($Node -is [System.Management.Automation.PSCustomObject]) {
        $toRemove = @()
        foreach ($prop in $Node.PSObject.Properties) {
            $name = $prop.Name
            $val  = $prop.Value
            $low  = $name.ToLowerInvariant()

            # Drop well-known secret-bearing field names.
            if ($low -in @('authorization', 'apikey', 'api_key', 'secret',
                           'clientsecret', 'client_secret', 'accesstoken',
                           'access_token', 'refreshtoken', 'refresh_token',
                           'password', 'passwd', 'token')) {
                # Authorization is allowed ONLY on the zai-coding-plan
                # provider we explicitly route through Anubis. Top-level
                # scope prevents blanket removal.
                if ($low -eq 'authorization' -and $name -eq 'Authorization') {
                    # Keep but log a warning so operator sees what survived.
                    Write-Warning "[setup] retained Authorization header at $name (z.ai provider only)"
                    continue
                }
                $toRemove += $name
                continue
            }
            # Recurse.
            Scrub-Secrets -Node $val
        }
        foreach ($r in $toRemove) {
            $Node.PSObject.Properties.Remove($r)
            Write-Warning "[setup] scrubbed secret field: $r"
        }
    }
    elseif ($Node -is [System.Collections.IList]) {
        foreach ($item in $Node) { Scrub-Secrets -Node $item }
    }
}

Write-Host "[setup] scrubbing secrets from config copy (S5 fix)"
Scrub-Secrets -Node $cfg

# 4. Force no-anubis variant: z.ai DIRECT (strip Anubis routing headers too).
if ($cfg.provider.PSObject.Properties.Name -contains 'zai-coding-plan') {
    $zai = $cfg.provider.'zai-coding-plan'
    if (-not $zai.options) {
        $zai | Add-Member -NotePropertyName options -NotePropertyValue ([PSCustomObject]@{})
    }
    $zai.options.baseURL = $DirectZaiBaseUrl
    # Strip Anubis routing headers if present.
    if ($zai.options.headers) {
        foreach ($h in @('x-anubis-target', 'sleeve-harness', 'sleeve-provider')) {
            if ($zai.options.headers.PSObject.Properties.Name -contains $h) {
                $zai.options.headers.PSObject.Properties.Remove($h)
            }
        }
    }
    Write-Host "[setup] zai-coding-plan.baseURL -> $DirectZaiBaseUrl (DIRECT)"
}

$cfg | ConvertTo-Json -Depth 20 | Set-Content -Path $NoAnubisJson -Encoding UTF8
Write-Host "[setup] wrote no-anubis  config: $NoAnubisJson"

# 5. Mutate z.ai baseURL to route through Anubis, save with-anubis variant.
if ($cfg.provider.PSObject.Properties.Name -contains 'zai-coding-plan') {
    $zai = $cfg.provider.'zai-coding-plan'
    $zai.options.baseURL = $AnubisBaseUrl
    Write-Host "[setup] zai-coding-plan.baseURL -> $AnubisBaseUrl (Anubis)"
} else {
    Write-Warning "Source config has no 'zai-coding-plan' provider; with-anubis config will be identical to no-anubis."
}

$cfg | ConvertTo-Json -Depth 20 | Set-Content -Path $WithAnubisJson -Encoding UTF8
Write-Host "[setup] wrote with-anubis config: $WithAnubisJson"
Write-Host "[setup] DONE. Both configs ready for harness/run_side_by_side.py."
