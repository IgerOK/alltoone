<#
  Install alltoone and optionally set up a project for the handoff workflow.

    .\scripts\install.ps1
    .\scripts\install.ps1 -Target D:\work\my-project
    .\scripts\install.ps1 -Target . -Force      # overwrite existing files

  Editable install: `git pull` is enough to update, no reinstall.
#>
param(
    [string]$Target = "",
    [string]$Python = "python",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

function Fail([string]$message) {
    Write-Host "error: $message" -ForegroundColor Red
    exit 1
}

# 1. Python 3.10+
$version = & $Python -c "import sys; print('.'.join(map(str, sys.version_info[:2])))" 2>$null
if (-not $? -or -not $version) {
    Fail "python not found; install Python 3.10+ from python.org (tick 'Add to PATH')"
}
if ([version]$version -lt [version]"3.10") {
    Fail "python $version is too old; 3.10+ is required"
}
Write-Host "python  $version"

# 2. The CLI itself.
& $Python -m pip install --quiet --upgrade -e $repo
if (-not $?) {
    Fail "pip install failed"
}
& $Python -m alltoone --version

if (-not $Target) {
    Write-Host ""
    Write-Host "Installed. Next: add the workflow to a project with"
    Write-Host "  .\scripts\install.ps1 -Target <dir>"
    exit 0
}

# 3. The OpenCode part: plugin, commands, AGENTS.md, options.
#    `.opencode/handoff/` is never copied: it holds this project's session state.
$targetPath = (Resolve-Path $Target).Path
if (-not (Test-Path $targetPath -PathType Container)) {
    Fail "not a directory: $Target"
}
if ($targetPath -eq (Resolve-Path $repo).Path) {
    Fail "target is the repository itself"
}

$files = @(
    ".opencode\plugins\context-budget",
    ".opencode\commands\handoff.md",
    ".opencode\commands\resume.md",
    "AGENTS.md",
    "opencode.jsonc"
)

foreach ($rel in $files) {
    $from = Join-Path $repo $rel
    $to = Join-Path $targetPath $rel
    if ((Test-Path $to) -and -not $Force) {
        Write-Host "skip    $rel (exists; use -Force to overwrite)"
        continue
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $to) | Out-Null
    Copy-Item $from $to -Recurse -Force
    Write-Host "copied  $rel"
}

Write-Host ""
Write-Host "Done. Open $targetPath in OpenCode, work as usual."
Write-Host "At 85% context run /handoff, then open a new chat and run /resume."
