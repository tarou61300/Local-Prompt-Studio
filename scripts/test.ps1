param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "The development environment is missing. Run scripts\setup_dev.ps1 first."
}
$DirectorySeparator = [System.IO.Path]::DirectorySeparatorChar
$AltDirectorySeparator = [System.IO.Path]::AltDirectorySeparatorChar
$WorkspacePrefix = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd(
    $DirectorySeparator,
    $AltDirectorySeparator
) + $DirectorySeparator
$TestTemporaryDirectory = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot ".tmp\pytest"))
if (-not $TestTemporaryDirectory.StartsWith(
    $WorkspacePrefix,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Test temporary directory is outside the project workspace: $TestTemporaryDirectory"
}
New-Item -ItemType Directory -Path $TestTemporaryDirectory -Force | Out-Null
$env:TMP = $TestTemporaryDirectory
$env:TEMP = $TestTemporaryDirectory
& $Python -m pytest
