# Clean-check: run the CI quality gates in a fresh git worktree.
#
# Why: a defect that only reproduces on a clean checkout (missing ignored
# directory, a file that is not committed, an untracked config) is invisible in
# the working tree. CI run 18 failed exactly this way: scheduler.py opened
# logs/scheduler.log before creating the directory, and the local logs/ folder
# hid it (commit 3ae08e1).
#
# The script checks out HEAD into a temporary worktree, which contains only
# tracked files: no .env, no .venv, no logs/, no debug/. It then runs the same
# three gates as .github/workflows/ci.yml.
#
# Run it AFTER the commit and BEFORE the push: it verifies exactly the tree that
# CI will check out. Uncommitted files are not in the worktree, so a run before
# the commit reports them as missing — that is the intended behaviour, not a bug.
#
# Dependencies: the main tree's .venv is reused on purpose. `uv sync --frozen`
# is what CI verifies; this script verifies that the code works without the
# untracked files.
#
# Usage:
#   powershell -File scripts/clean_check.ps1
#   powershell -File scripts/clean_check.ps1 -Keep   # keep the worktree for debugging

[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$Keep
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$worktree = Join-Path ([System.IO.Path]::GetTempPath()) ("selima_clean_check_" + [guid]::NewGuid().ToString("N").Substring(0, 8))

if (-not $Python) {
    $candidate = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path $candidate) {
        $Python = $candidate
    } else {
        $Python = "python"
    }
}

# The same list as CLEAN_SET in .github/workflows/ci.yml. Keep them in sync:
# the script must not become a weaker gate than CI.
$cleanSet = @(
    "config/commissions.py"
    "src/backtest/costs.py"
    "src/backtest/dividends.py"
    "src/backtest/evaluate.py"
    "src/backtest/event_study.py"
    "src/backtest/exit_policy.py"
    "src/backtest/heatmap.py"
    "src/backtest/hypothesis_log.py"
    "src/backtest/null_test.py"
    "src/backtest/portfolio.py"
    "src/backtest/simulator.py"
    "src/backtest/verdict.py"
    "src/backtest/walk_forward.py"
    "src/backtest/weekend.py"
    "src/market_data/preparation.py"
    "src/strategies/_null_entry"
    "src/strategies/_template"
    "src/strategies/strategy_registry.py"
    "src/trading/pending_recovery.py"
    "tests"
)

Write-Host "clean check: HEAD $(git -C $root rev-parse --short HEAD)"
Write-Host "worktree:   $worktree"

git -C $root worktree add $worktree HEAD --detach | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "git worktree add failed"
}

$failed = 0
try {
    Push-Location $worktree
    try {
        Write-Host "`n[1/3] ruff"
        & $Python -m ruff check @cleanSet
        if ($LASTEXITCODE -ne 0) { $failed = 1 }

        Write-Host "`n[2/3] mypy"
        & $Python -m mypy --follow-imports=silent @cleanSet
        if ($LASTEXITCODE -ne 0) { $failed = 1 }

        Write-Host "`n[3/3] pytest"
        & $Python -m pytest -q
        if ($LASTEXITCODE -ne 0) { $failed = 1 }
    } finally {
        Pop-Location
    }
} finally {
    if ($Keep) {
        Write-Host "`nworktree kept: $worktree"
    } else {
        git -C $root worktree remove --force $worktree | Out-Null
    }
}

if ($failed -ne 0) {
    Write-Host "`nclean check FAILED"
    exit 1
}
Write-Host "`nclean check passed"
exit 0
