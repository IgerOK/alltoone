<#
  Remove what the installer placed, and the package itself.

    .\scripts\uninstall.ps1                          # package only
    .\scripts\uninstall.ps1 -Target D:\work\my-project
    .\scripts\uninstall.ps1 -Target . -Purge          # also drop .opencode/handoff state
    .\scripts\uninstall.ps1 -Target . -Force          # remove even changed files

  A file that differs from the shipped copy is kept: it may be the user's own
  agreement or config, and deleting it silently would be data loss.
#>
param(
    [string]$Target = "",
    [string]$Python = "python",
    [switch]$Purge,
    [switch]$KeepPackage,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

function Fail([string]$message) {
    Write-Host "error: $message" -ForegroundColor Red
    exit 1
}

# Kept identical to install.ps1; a test keeps the two lists in sync.
$files = @(
    ".opencode\plugins\context-budget",
    ".opencode\commands\handoff.md",
    ".opencode\commands\resume.md",
    ".opencode\skills\skill-template.md",
    "AGENTS.md",
    "opencode.jsonc"
)

function Same-Content([string]$left, [string]$right) {
    if (-not (Test-Path $right)) { return $false }
    if ((Test-Path $left -PathType Leaf)) {
        if (-not (Test-Path $right -PathType Leaf)) { return $false }
        return (Get-FileHash $left).Hash -eq (Get-FileHash $right).Hash
    }
    if (-not (Test-Path $right -PathType Container)) { return $false }
    $pattern = [regex]::Escape($left.TrimEnd('\')) + '\\'
    $a = Get-ChildItem -Recurse -File -Force $left |
        ForEach-Object { $_.FullName -replace $pattern, "" } |
        ForEach-Object { $_ + ":" + (Get-FileHash (Join-Path $left $_)).Hash }
    $pattern = [regex]::Escape($right.TrimEnd('\')) + '\\'
    $b = Get-ChildItem -Recurse -File -Force $right |
        ForEach-Object { $_.FullName -replace $pattern, "" } |
        ForEach-Object { $_ + ":" + (Get-FileHash (Join-Path $right $_)).Hash }
    return -not (Compare-Object $a $b)
}

if (-not $Target) {
    if (-not $KeepPackage) {
        & $Python -m pip uninstall --quiet -y alltoone
        Write-Host "removed package alltoone"
    }
    Write-Host ""
    Write-Host "Nothing else was touched. To clean a project as well:"
    Write-Host "  .\scripts\uninstall.ps1 -Target <dir> [-Purge]"
    exit 0
}

$targetPath = (Resolve-Path $Target -ErrorAction SilentlyContinue).Path
if (-not $targetPath -or -not (Test-Path $targetPath -PathType Container)) {
    Fail "not a directory: $Target"
}
if ($targetPath -eq (Resolve-Path $repo).Path) {
    Fail "target is the repository itself"
}

foreach ($rel in $files) {
    $to = Join-Path $targetPath $rel
    if (-not (Test-Path $to)) {
        Write-Host "absent  $rel"
        continue
    }
    if (-not $Force -and -not (Same-Content (Join-Path $repo $rel) $to)) {
        Write-Host "kept    $rel (changed since install; use -Force)"
        continue
    }
    Remove-Item $to -Recurse -Force
    Write-Host "removed $rel"
}

# Personal skills stay: they were written for this project, not installed.
$skills = Join-Path $targetPath ".opencode\skills"
if ((Test-Path $skills) -and -not (Get-ChildItem -Force $skills)) {
    Remove-Item $skills -Force
}

if ($Purge) {
    $state = Join-Path $targetPath ".opencode\handoff"
    if (Test-Path $state) {
        Remove-Item $state -Recurse -Force
        Write-Host "removed .opencode/handoff (state and pack)"
    }
}

# Only empty leftovers of ours: a project may keep other .opencode content.
foreach ($dir in @(".opencode\commands", ".opencode\plugins", ".opencode")) {
    $path = Join-Path $targetPath $dir
    if ((Test-Path $path) -and -not (Get-ChildItem -Force $path)) {
        Remove-Item $path -Force
    }
}

if (-not $KeepPackage) {
    & $Python -m pip uninstall --quiet -y alltoone
    Write-Host "removed package alltoone"
}

Write-Host ""
Write-Host "Done. Project files were not touched."
if (-not $Purge) {
    Write-Host "The handoff state is still in $targetPath\.opencode\handoff (add -Purge to drop it)."
}
