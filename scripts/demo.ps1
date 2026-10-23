# Happy-flow demo of actiontrack on the bundled sample notes (Windows PowerShell 5.1+).
#   .\scripts\demo.ps1            # runs everything in a temporary folder, nothing is committed or sent
#
# Steps: ingest the sample notes -> list -> list overdue/owner -> done -> followup (preview + handoff file).
# A follow-up only PREPARES a handoff file; no e-mail is sent and nothing is created in Gmail.

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py -or $py -like '*WindowsApps*') { $py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe' }
if (-not (Test-Path $py)) { throw "Python 3.12+ not found. Install it (winget install Python.Python.3.12) and retry." }

$work = Join-Path ([IO.Path]::GetTempPath()) ("actiontrack-demo-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $work | Out-Null
$notes = Join-Path $work 'notes'
Copy-Item (Join-Path $repo 'tests\fixtures\notes') $notes -Recurse
$env:PYTHONPATH = Join-Path $repo 'src'
$env:PYTHONUTF8 = '1'
$cfg = Join-Path $repo 'actiontrack.example.toml'
$db = Join-Path $work 'actiontrack.db'
$common = @('--config', $cfg, '--db', $db, '--today', '2026-10-22')

function Step($title, [string[]]$cliArgs) {
    Write-Host ""
    Write-Host ("=== " + $title) -ForegroundColor Cyan
    Write-Host ("> actiontrack " + ($cliArgs -join ' ')) -ForegroundColor DarkGray
    & $py -m actiontrack @common @cliArgs
    Write-Host ("(exit code " + $LASTEXITCODE + ")") -ForegroundColor DarkGray
}

Push-Location $work
try {
    Step '1. Ingest the meeting notes (exit 5 = one note, scratch.md, is binary and was skipped)' @('ingest', $notes)
    Step '2. List open action items' @('list')
    Step '3. Only overdue items of Priya Nair' @('list', '--owner', 'Priya Nair', '--status', 'overdue')
    Step '4. A bare shared first name is refused (exit 6)' @('list', '--owner', 'Priya')
    Step '5. Owners that could not be pinned to one person' @('list', '--ambiguous')
    Step '6. Mark one item done' @('done', 'AT-0008')
    Step '7. Preview the follow-up e-mail for Priya Nair (nothing is written)' @('followup', 'Priya Nair', '--dry-run')
    Step '8. Prepare the handoff file for the draft (a draft is NOT created in Gmail by this tool)' @('followup', 'Priya Nair', '--yes')
    Write-Host ""
    Write-Host "Work folder: $work" -ForegroundColor Yellow
    Write-Host "Handoff file(s): $(Join-Path $work '.actiontrack\handoff')" -ForegroundColor Yellow
} finally {
    Pop-Location
}
