param(
    [Parameter(Mandatory = $true)][string]$TestSummary
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

$Python = Resolve-WorkspaceChild (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "The development environment is missing. Run scripts\setup_dev.ps1 first."
}
$Version = (Get-Content -LiteralPath (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$') {
    throw "VERSION must contain a semantic version such as 1.0.0 or 1.1.0-beta.1."
}
$IsPrerelease = $Version.Contains("-")
$VersionValues = @(
    & $Python -c `
        "import sys; sys.path.insert(0, sys.argv[1]); from core.version import APP_VERSION, APP_RELEASE_DATE, REPOSITORY_URL; print(APP_VERSION); print(APP_RELEASE_DATE); print(REPOSITORY_URL)" `
        (Join-Path $ProjectRoot "src")
)
if ($LASTEXITCODE -ne 0 -or $VersionValues.Count -ne 3) {
    throw "Application version metadata could not be read."
}
if ($VersionValues[0].Trim() -ne $Version) {
    throw "VERSION and src\core\version.py are inconsistent."
}
$ReleaseDate = $VersionValues[1].Trim()
$RepositoryUrl = $VersionValues[2].Trim()
$VersionCore = ($Version -split '-', 2)[0].Split('.')
$ExpectedNumericVersion = "$($VersionCore[0]).$($VersionCore[1]).$($VersionCore[2]).0"
$ExpectedVersionTuple = "($($VersionCore[0]), $($VersionCore[1]), $($VersionCore[2]), 0)"
$ReadmeText = Get-Content -LiteralPath (Join-Path $ProjectRoot "README.md") -Raw
$ChangelogText = Get-Content -LiteralPath (Join-Path $ProjectRoot "CHANGELOG.md") -Raw
$WindowsVersionText = Get-Content -LiteralPath (Join-Path $ProjectRoot "packaging\version_info.txt") -Raw
if (-not $ReadmeText.StartsWith("# Local Prompt Studio v$Version")) {
    throw "README version is inconsistent with VERSION."
}
if (-not $ReadmeText.Contains($RepositoryUrl)) {
    throw "README repository URL is inconsistent with application metadata."
}
if (-not $ChangelogText.Contains("## $Version") -or -not $ChangelogText.Contains($ReleaseDate)) {
    throw "CHANGELOG version or release date is inconsistent."
}
if (
    -not $WindowsVersionText.Contains("filevers=$ExpectedVersionTuple") -or
    -not $WindowsVersionText.Contains("prodvers=$ExpectedVersionTuple") -or
    -not $WindowsVersionText.Contains("StringStruct(u'FileVersion', u'$ExpectedNumericVersion')") -or
    -not $WindowsVersionText.Contains("StringStruct(u'ProductVersion', u'$Version')")
) {
    throw "Windows version metadata is inconsistent."
}
$SourceCommit = (& git -C $ProjectRoot rev-parse HEAD).Trim()
$SourceBranch = (& git -C $ProjectRoot branch --show-current).Trim()
if (-not $SourceCommit -or -not $SourceBranch) {
    throw "Git source metadata could not be read."
}
$PythonVersion = (& $Python -c "import platform; print(platform.python_version())").Trim()
$PyInstallerVersion = (& $Python -m PyInstaller --version).Trim()
$BridgeSource = Get-Content -LiteralPath (
    Join-Path $ProjectRoot "comfyui_extension\MMH3PromptBridge\__init__.py"
) -Raw
$BridgeVersionMatch = [regex]::Match($BridgeSource, '(?m)^API_VERSION\s*=\s*"([^"]+)"')
if (-not $BridgeVersionMatch.Success) {
    throw "ComfyUI Prompt Bridge version could not be read."
}
$BridgeVersion = $BridgeVersionMatch.Groups[1].Value
$ReleaseKind = if ($IsPrerelease) { "Community test / GitHub pre-release" } else { "Stable release" }
foreach ($Variant in @("cpu", "vulkan")) {
    $Server = Resolve-WorkspaceChild (Join-Path $ProjectRoot "runtime\$Variant\llama-server.exe")
    if (-not (Test-Path -LiteralPath $Server -PathType Leaf)) {
        throw "Required $Variant runtime is missing: $Server"
    }
}

$DistRoot = Resolve-WorkspaceChild (Join-Path $ProjectRoot "dist")
$PyInstallerOutput = Resolve-WorkspaceChild (Join-Path $DistRoot "LocalPromptStudio")
$PortableName = "Local-Prompt-Studio-v$Version-win-x64-portable"
$ReleaseRoot = Resolve-WorkspaceChild (Join-Path $ProjectRoot "release")
$DistributionRoot = Resolve-WorkspaceChild (Join-Path $ReleaseRoot $PortableName)
$ApplicationRoot = if ($IsPrerelease) {
    Resolve-WorkspaceChild (Join-Path $DistributionRoot "LocalPromptStudio")
}
else {
    $DistributionRoot
}
$ZipPath = Resolve-WorkspaceChild (Join-Path $ReleaseRoot "$PortableName.zip")
$ChecksumsPath = Resolve-WorkspaceChild (Join-Path $ReleaseRoot "SHA256SUMS.txt")
$ManifestPath = Resolve-WorkspaceChild (Join-Path $ReleaseRoot "RELEASE_MANIFEST.txt")
$ManifestTemplatePath = Resolve-WorkspaceChild (
    Join-Path $ProjectRoot "packaging\RELEASE_MANIFEST.template.txt"
)
$BuildTemporaryRoot = Resolve-WorkspaceChild (Join-Path $ProjectRoot ".tmp\build")
$PyInstallerConfigRoot = Resolve-WorkspaceChild (Join-Path $ProjectRoot ".tmp\pyinstaller-config")
New-Item -ItemType Directory -Path $BuildTemporaryRoot -Force | Out-Null
New-Item -ItemType Directory -Path $PyInstallerConfigRoot -Force | Out-Null
$PreviousTemp = $env:TEMP
$PreviousTmp = $env:TMP
$PreviousPyInstallerConfig = $env:PYINSTALLER_CONFIG_DIR
$env:TEMP = $BuildTemporaryRoot
$env:TMP = $BuildTemporaryRoot
$env:PYINSTALLER_CONFIG_DIR = $PyInstallerConfigRoot

Push-Location $ProjectRoot
try {
    & $Python -m PyInstaller --noconfirm --clean "LocalPromptStudio.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }
}
finally {
    Pop-Location
    $env:TEMP = $PreviousTemp
    $env:TMP = $PreviousTmp
    $env:PYINSTALLER_CONFIG_DIR = $PreviousPyInstallerConfig
}
if (-not (Test-Path -LiteralPath $PyInstallerOutput -PathType Container)) {
    throw "PyInstaller onedir output was not created."
}
New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
if (Test-Path -LiteralPath $DistributionRoot) {
    Remove-Item -LiteralPath $DistributionRoot -Recurse -Force
}
if ($IsPrerelease) {
    New-Item -ItemType Directory -Path $DistributionRoot -Force | Out-Null
}
Move-Item -LiteralPath $PyInstallerOutput -Destination $ApplicationRoot

$DocumentationFiles = @(
    "README.md",
    "LICENSE",
    "THIRD_PARTY_LICENSES.md",
    "CHANGELOG.md",
    "VERSION"
)
foreach ($Name in $DocumentationFiles) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Name) -Destination $ApplicationRoot -Force
}
if ($IsPrerelease) {
    foreach ($Name in $DocumentationFiles) {
        Copy-Item -LiteralPath (Join-Path $ProjectRoot $Name) -Destination $DistributionRoot -Force
    }
    $BridgeSourceRoot = Resolve-WorkspaceChild (
        Join-Path $ProjectRoot "comfyui_extension\MMH3PromptBridge"
    )
    $BridgeRoot = Resolve-WorkspaceChild (
        Join-Path $DistributionRoot "ComfyUI-Bridge\MMH3PromptBridge"
    )
    $BridgeJsRoot = Resolve-WorkspaceChild (Join-Path $BridgeRoot "js")
    New-Item -ItemType Directory -Path $BridgeJsRoot -Force | Out-Null
    foreach ($Name in @("__init__.py", "README.md", "LICENSE")) {
        Copy-Item -LiteralPath (Join-Path $BridgeSourceRoot $Name) -Destination $BridgeRoot -Force
    }
    Copy-Item -LiteralPath (Join-Path $BridgeSourceRoot "js\mmh3_bridge.js") `
        -Destination $BridgeJsRoot -Force
}
$DataRoot = Resolve-WorkspaceChild (Join-Path $ApplicationRoot "data")
$LicenseRoot = Resolve-WorkspaceChild (Join-Path $ApplicationRoot "licenses")
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
New-Item -ItemType Directory -Path $LicenseRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\PORTABLE_DATA_README.txt") `
    -Destination (Join-Path $DataRoot "README.txt") -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "licenses\llama.cpp-LICENSE.txt") `
    -Destination $LicenseRoot -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "licenses\LGPL-3.0.txt") `
    -Destination $LicenseRoot -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "licenses\LLVM-LICENSE.txt") `
    -Destination $LicenseRoot -Force

$PythonBase = (& $Python -c "import sys; print(sys.base_prefix)").Trim()
$PythonLicense = Join-Path $PythonBase "LICENSE.txt"
if (-not (Test-Path -LiteralPath $PythonLicense -PathType Leaf)) {
    throw "Python license file was not found: $PythonLicense"
}
Copy-Item -LiteralPath $PythonLicense -Destination (Join-Path $LicenseRoot "Python-LICENSE.txt") -Force
$PyInstallerLicense = Join-Path $ProjectRoot ".venv\Lib\site-packages\pyinstaller-6.21.0.dist-info\licenses\COPYING.txt"
if (-not (Test-Path -LiteralPath $PyInstallerLicense -PathType Leaf)) {
    throw "PyInstaller license file was not found."
}
Copy-Item -LiteralPath $PyInstallerLicense `
    -Destination (Join-Path $LicenseRoot "PyInstaller-COPYING.txt") -Force

Get-ChildItem -LiteralPath $DistributionRoot -Recurse -Force -File |
    Where-Object { $_.Name -eq ".gitkeep" } |
    Remove-Item -Force

& $Python (Join-Path $ProjectRoot "scripts\audit_release.py") $DistributionRoot
if ($LASTEXITCODE -ne 0) {
    throw "Release content audit failed."
}

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
& $Python (Join-Path $ProjectRoot "scripts\create_release_zip.py") `
    $DistributionRoot $ZipPath
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $ZipPath -PathType Leaf)) {
    throw "Release ZIP creation failed."
}

$MainExe = Join-Path $ApplicationRoot "LocalPromptStudio.exe"
$CpuServer = Join-Path $ApplicationRoot "_internal\runtime\cpu\llama-server.exe"
$VulkanServer = Join-Path $ApplicationRoot "_internal\runtime\vulkan\llama-server.exe"
$ZipHash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$ExeHash = (Get-FileHash -LiteralPath $MainExe -Algorithm SHA256).Hash.ToLowerInvariant()
$CpuServerHash = (Get-FileHash -LiteralPath $CpuServer -Algorithm SHA256).Hash.ToLowerInvariant()
$VulkanServerHash = (Get-FileHash -LiteralPath $VulkanServer -Algorithm SHA256).Hash.ToLowerInvariant()
$ApplicationRelative = if ($IsPrerelease) { "$PortableName/LocalPromptStudio" } else { $PortableName }
$ChecksumLines = @(
    "{0}  {1}" -f $ZipHash, (Split-Path $ZipPath -Leaf)
    "{0}  {1}" -f $ExeHash, "$ApplicationRelative/LocalPromptStudio.exe"
    "{0}  {1}" -f $CpuServerHash, "$ApplicationRelative/_internal/runtime/cpu/llama-server.exe"
    "{0}  {1}" -f $VulkanServerHash, "$ApplicationRelative/_internal/runtime/vulkan/llama-server.exe"
)
Set-Content -LiteralPath $ChecksumsPath -Value $ChecksumLines -Encoding UTF8

$LlamaVersion = (Get-Content -LiteralPath (Join-Path $ProjectRoot "runtime\LLAMA_CPP_VERSION_PIN.txt") -Raw).Trim()
$LlamaCommit = (Get-Content -LiteralPath (Join-Path $ProjectRoot "runtime\LLAMA_CPP_COMMIT_PIN.txt") -Raw).Trim()
$CpuAssetSha256 = (Get-Content -LiteralPath (Join-Path $ProjectRoot "runtime\LLAMA_CPP_CPU_SHA256_PIN.txt") -Raw).Trim()
$VulkanAssetSha256 = (Get-Content -LiteralPath (Join-Path $ProjectRoot "runtime\LLAMA_CPP_VULKAN_SHA256_PIN.txt") -Raw).Trim()
$CpuSourceUrl = (Get-Content -LiteralPath (Join-Path $ProjectRoot "runtime\cpu\LLAMA_CPP_SOURCE_URL.txt") -Raw).Trim()
$VulkanSourceUrl = (Get-Content -LiteralPath (Join-Path $ProjectRoot "runtime\vulkan\LLAMA_CPP_SOURCE_URL.txt") -Raw).Trim()
$CpuAssetName = [System.IO.Path]::GetFileName($CpuSourceUrl)
$VulkanAssetName = [System.IO.Path]::GetFileName($VulkanSourceUrl)
$DistributionContents = if ($IsPrerelease) {
    "Portable application, ComfyUI Prompt Bridge v$BridgeVersion source, user documentation, and required licenses"
}
else {
    "Portable application, user documentation, and required licenses"
}
$Manifest = Get-Content -LiteralPath $ManifestTemplatePath -Raw
$Manifest = $Manifest.Replace("{{APP_VERSION}}", $Version)
$Manifest = $Manifest.Replace("{{REPOSITORY_URL}}", $RepositoryUrl)
$Manifest = $Manifest.Replace("{{ZIP_FILENAME}}", (Split-Path $ZipPath -Leaf))
$Manifest = $Manifest.Replace("{{RELEASE_DATE}}", $ReleaseDate)
$Manifest = $Manifest.Replace("{{RELEASE_KIND}}", $ReleaseKind)
$Manifest = $Manifest.Replace("{{SOURCE_COMMIT}}", $SourceCommit)
$Manifest = $Manifest.Replace("{{SOURCE_BRANCH}}", $SourceBranch)
$Manifest = $Manifest.Replace("{{PYTHON_VERSION}}", $PythonVersion)
$Manifest = $Manifest.Replace("{{PYINSTALLER_VERSION}}", $PyInstallerVersion)
$Manifest = $Manifest.Replace("{{BRIDGE_VERSION}}", $BridgeVersion)
$Manifest = $Manifest.Replace("{{TEST_SUMMARY}}", $TestSummary)
$Manifest = $Manifest.Replace("{{DISTRIBUTION_CONTENTS}}", $DistributionContents)
$Manifest = $Manifest.Replace("{{LLAMA_CPP_VERSION}}", $LlamaVersion)
$Manifest = $Manifest.Replace("{{LLAMA_CPP_COMMIT}}", $LlamaCommit)
$Manifest = $Manifest.Replace("{{CPU_ASSET_NAME}}", $CpuAssetName)
$Manifest = $Manifest.Replace("{{CPU_ASSET_SHA256}}", $CpuAssetSha256)
$Manifest = $Manifest.Replace("{{VULKAN_ASSET_NAME}}", $VulkanAssetName)
$Manifest = $Manifest.Replace("{{VULKAN_ASSET_SHA256}}", $VulkanAssetSha256)
$Manifest = $Manifest.Replace("{{EXE_SHA256}}", $ExeHash)
$Manifest = $Manifest.Replace("{{ZIP_SHA256}}", $ZipHash)
Set-Content -LiteralPath $ManifestPath -Value $Manifest -Encoding UTF8

Write-Host "Distribution folder: $DistributionRoot"
Write-Host "Portable onedir: $ApplicationRoot"
Write-Host "Release ZIP: $ZipPath"
Write-Host "Checksums: $ChecksumsPath"
Write-Host "Manifest: $ManifestPath"
