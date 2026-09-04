# Weekly Hedge Fund Tracker run: fetch latest 13Fs, regenerate the consensus
# report, commit/push history to GitHub (best-effort), then open the report.
$ErrorActionPreference = "Continue"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

$LogDir = Join-Path $ProjectDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("run_{0}.log" -f (Get-Date -Format "yyyy-MM-dd_HHmmss"))

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $LogFile -Value $line
}

Log "=== Weekly Hedge Fund Tracker run starting ==="

Log "Running fetch-all..."
$fetchOutput = & py hedge_tracker.py fetch-all 2>&1 | Out-String
Add-Content -Path $LogFile -Value $fetchOutput

Log "Running theme_report.py..."
$reportOutput = & py theme_report.py 2>&1 | Out-String
Add-Content -Path $LogFile -Value $reportOutput

# Best-effort: commit and push updated data/reports to GitHub so history
# accumulates there too. Never fail the run if this doesn't work (e.g. no
# cached credentials in this session) -- the local report still gets opened.
try {
    git add -A 2>&1 | Out-Null
    $hasChanges = git status --porcelain
    if ($hasChanges) {
        $commitMsg = "Weekly 13F fetch + consensus report {0}" -f (Get-Date -Format "yyyy-MM-dd")
        git commit -m "$commitMsg`n`nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>" 2>&1 | ForEach-Object { Log $_ }
        git push origin main 2>&1 | ForEach-Object { Log $_ }
        Log "Committed and pushed weekly update to GitHub."
    } else {
        Log "No changes to commit this week."
    }
} catch {
    Log "Git commit/push step failed (non-fatal): $_"
}

# Open the freshest report so it's visible next time you're at the machine.
$latestReport = Get-ChildItem -Path (Join-Path $ProjectDir "reports") -Filter "*.md" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1

if ($latestReport) {
    Log ("Opening report: {0}" -f $latestReport.FullName)
    Start-Process notepad.exe -ArgumentList $latestReport.FullName
} else {
    Log "No report file found to open."
}

Log "=== Weekly run finished ==="
