param(
    [switch]$Mock,
    [switch]$SkipSetup,
    [switch]$Portable
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "The development environment is missing. Run scripts\setup_dev.ps1 first."
}

$Arguments = @((Join-Path $ProjectRoot "src\main.py"))
if ($Mock) {
    $Arguments += @("--mock", "--skip-setup", "--portable-data", (Join-Path $ProjectRoot ".dev-data"))
}
elseif ($SkipSetup) {
    $Arguments += "--skip-setup"
}
if ($Portable -and (-not $Mock)) {
    $Arguments += @("--portable-data", (Join-Path $ProjectRoot ".dev-data\real-cpu"))
}
& $Python @Arguments
