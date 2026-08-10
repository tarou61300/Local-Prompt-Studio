param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
    }
    if (-not $PythonCommand) {
        throw "Python 3.12 was not found. It is required for the development setup."
    }
    & $PythonCommand.Source -m venv (Join-Path $ProjectRoot ".venv")
}

& $VenvPython -m pip install --no-cache-dir --upgrade pip
& $VenvPython -m pip install --no-cache-dir -r (Join-Path $ProjectRoot "requirements-dev.txt")
Write-Host "Development environment setup completed."
