param(
    [string]$ZipPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$DirectorySeparator = [System.IO.Path]::DirectorySeparatorChar
$AltDirectorySeparator = [System.IO.Path]::AltDirectorySeparatorChar
$WorkspacePrefix = $ProjectRoot.TrimEnd($DirectorySeparator, $AltDirectorySeparator) + $DirectorySeparator

function Resolve-WorkspaceChild {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Resolved = [System.IO.Path]::GetFullPath($Path)
    if (-not $Resolved.StartsWith($WorkspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside the project workspace: $Resolved"
    }
    return $Resolved
}

$Version = (Get-Content -LiteralPath (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
$PortableName = "MMH3-Prompt-Builder-v$Version-win-x64-portable"
if (-not $ZipPath) {
    $ZipPath = Join-Path $ProjectRoot "release\$PortableName.zip"
}
$ZipPath = Resolve-WorkspaceChild $ZipPath
if (-not (Test-Path -LiteralPath $ZipPath -PathType Leaf)) {
    throw "Release ZIP was not found: $ZipPath"
}
$VerificationRoot = Resolve-WorkspaceChild (
    (Join-Path $ProjectRoot ".tmp\release-verification\日本語 パス $([guid]::NewGuid().ToString('N'))")
)
$BaselineServerIds = @(
    Get-Process -Name "llama-server" -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Id
)
New-Item -ItemType Directory -Path $VerificationRoot -Force | Out-Null
try {
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $VerificationRoot
    $PortableRoot = Resolve-WorkspaceChild (Join-Path $VerificationRoot $PortableName)
    $Python = Resolve-WorkspaceChild (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
    & $Python (Join-Path $ProjectRoot "scripts\audit_release.py") $PortableRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Expanded release audit failed."
    }

    $LocalAppDataSentinel = Resolve-WorkspaceChild (Join-Path $VerificationRoot "localappdata-sentinel")
    $AppDataSentinel = Resolve-WorkspaceChild (Join-Path $VerificationRoot "appdata-sentinel")
    $ProgramDataSentinel = Resolve-WorkspaceChild (Join-Path $VerificationRoot "programdata-sentinel")
    New-Item -ItemType Directory -Path $LocalAppDataSentinel -Force | Out-Null
    New-Item -ItemType Directory -Path $AppDataSentinel -Force | Out-Null
    New-Item -ItemType Directory -Path $ProgramDataSentinel -Force | Out-Null
    $PreviousLocalAppData = $env:LOCALAPPDATA
    $PreviousAppData = $env:APPDATA
    $PreviousProgramData = $env:PROGRAMDATA
    $env:LOCALAPPDATA = $LocalAppDataSentinel
    $env:APPDATA = $AppDataSentinel
    $env:PROGRAMDATA = $ProgramDataSentinel
    try {
        $Application = Join-Path $PortableRoot "MMH3PromptBuilder.exe"
        $Process = Start-Process -FilePath $Application `
            -ArgumentList @("--skip-setup", "--smoke-test") `
            -WorkingDirectory $PortableRoot `
            -WindowStyle Hidden `
            -PassThru `
            -Wait
        if ($Process.ExitCode -ne 0) {
            throw "Portable application smoke test failed with exit code $($Process.ExitCode)."
        }
        $RedirectSentinel = Resolve-WorkspaceChild (Join-Path $VerificationRoot "forbidden external data")
        $QuotedRedirectSentinel = '"' + $RedirectSentinel + '"'
        $RedirectProcess = Start-Process -FilePath $Application `
            -ArgumentList @("--skip-setup", "--smoke-test", "--portable-data", $QuotedRedirectSentinel) `
            -WorkingDirectory $PortableRoot `
            -WindowStyle Hidden `
            -PassThru `
            -Wait
        if ($RedirectProcess.ExitCode -ne 0) {
            throw "Portable redirect-resistance test failed with exit code $($RedirectProcess.ExitCode)."
        }
        if (Test-Path -LiteralPath $RedirectSentinel) {
            throw "The packaged application honored --portable-data outside its portable folder."
        }
        $FirstRunProcess = Start-Process -FilePath $Application `
            -ArgumentList "--smoke-test" `
            -WorkingDirectory $PortableRoot `
            -WindowStyle Hidden `
            -PassThru `
            -Wait
        if ($FirstRunProcess.ExitCode -ne 0) {
            throw "Portable first-run smoke test failed with exit code $($FirstRunProcess.ExitCode)."
        }
        foreach ($Relative in @("config.json", "history.sqlite3", "skills")) {
            if (Test-Path -LiteralPath (Join-Path $PortableRoot "data\$Relative")) {
                throw "First-run smoke test unexpectedly created data\$Relative."
            }
        }
    }
    finally {
        $env:LOCALAPPDATA = $PreviousLocalAppData
        $env:APPDATA = $PreviousAppData
        $env:PROGRAMDATA = $PreviousProgramData
    }

    $PortableLog = Join-Path $PortableRoot "data\mmh3-prompt-builder.log"
    if (-not (Test-Path -LiteralPath $PortableLog -PathType Leaf)) {
        throw "Portable data was not written beside the executable."
    }
    $UnexpectedExternalData = @(
        foreach ($Sentinel in @($LocalAppDataSentinel, $AppDataSentinel, $ProgramDataSentinel)) {
            Get-ChildItem -LiteralPath $Sentinel -Recurse -Force |
                Where-Object {
                    $Relative = $_.FullName.Substring($Sentinel.Length).TrimStart("\")
                    $Relative -ne "Microsoft" -and
                        $Relative -ne "Microsoft\Windows" -and
                        -not $Relative.StartsWith(
                            "Microsoft\Windows\Caches",
                            [System.StringComparison]::OrdinalIgnoreCase
                        )
                }
        }
    )
    if ($UnexpectedExternalData.Count -gt 0) {
        $UnexpectedExternalData | ForEach-Object {
            Write-Warning "Unexpected external data entry: $($_.FullName)"
        }
        throw "The portable application wrote application-owned data outside its portable folder."
    }

    foreach ($Variant in @("cpu", "vulkan")) {
        $Server = Join-Path $PortableRoot "_internal\runtime\$Variant\llama-server.exe"
        $RuntimeProcess = Start-Process -FilePath $Server `
            -ArgumentList "--version" `
            -WorkingDirectory (Split-Path $Server -Parent) `
            -WindowStyle Hidden `
            -PassThru `
            -Wait
        if ($RuntimeProcess.ExitCode -ne 0) {
            throw "$Variant llama-server dependency test failed."
        }
    }
    $NewServerProcesses = @(
        Get-Process -Name "llama-server" -ErrorAction SilentlyContinue |
            Where-Object { $_.Id -notin $BaselineServerIds }
    )
    if ($NewServerProcesses.Count -gt 0) {
        throw "A release-owned llama-server process remained after verification."
    }
    Write-Host "Expanded portable smoke test passed: $PortableRoot"
}
finally {
    if (Test-Path -LiteralPath $VerificationRoot) {
        Remove-Item -LiteralPath $VerificationRoot -Recurse -Force
    }
}
