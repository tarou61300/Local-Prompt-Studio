param()

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
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "VERSION must contain a semantic version such as 1.0.0."
}
$VersionValues = @(
    & $Python -c `
        "import sys; sys.path.insert(0, sys.argv[1]); from core.version import APP_VERSION, APP_RELEASE_DATE; print(APP_VERSION); print(APP_RELEASE_DATE)" `
        (Join-Path $ProjectRoot "src")
)
if ($LASTEXITCODE -ne 0 -or $VersionValues.Count -ne 2) {
    throw "Application version metadata could not be read."
}
if ($VersionValues[0].Trim() -ne $Version) {
    throw "VERSION and src\core\version.py are inconsistent."
}
$ReleaseDate = $VersionValues[1].Trim()
foreach ($Variant in @("cpu", "vulkan")) {
    $Server = Resolve-WorkspaceChild (Join-Path $ProjectRoot "runtime\$Variant\llama-server.exe")
    if (-not (Test-Path -LiteralPath $Server -PathType Leaf)) {
        throw "Required $Variant runtime is missing: $Server"
    }
}

$DistRoot = Resolve-WorkspaceChild (Join-Path $ProjectRoot "dist")
$PyInstallerOutput = Resolve-WorkspaceChild (Join-Path $DistRoot "MMH3PromptBuilder")
$PortableName = "MMH3-Prompt-Builder-v$Version-win-x64-portable"
$ReleaseRoot = Resolve-WorkspaceChild (Join-Path $ProjectRoot "release")
$PortableRoot = Resolve-WorkspaceChild (Join-Path $ReleaseRoot $PortableName)
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
    & $Python -m PyInstaller --noconfirm --clean "MMH3PromptBuilder.spec"
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
if (Test-Path -LiteralPath $PortableRoot) {
    Remove-Item -LiteralPath $PortableRoot -Recurse -Force
}
Move-Item -LiteralPath $PyInstallerOutput -Destination $PortableRoot

foreach ($Name in @("README.md", "LICENSE", "THIRD_PARTY_LICENSES.md", "CHANGELOG.md", "VERSION")) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Name) -Destination $PortableRoot -Force
}
$DataRoot = Resolve-WorkspaceChild (Join-Path $PortableRoot "data")
$LicenseRoot = Resolve-WorkspaceChild (Join-Path $PortableRoot "licenses")
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

Get-ChildItem -LiteralPath $PortableRoot -Recurse -Force -File |
    Where-Object { $_.Name -eq ".gitkeep" } |
    Remove-Item -Force

& $Python (Join-Path $ProjectRoot "scripts\audit_release.py") $PortableRoot
if ($LASTEXITCODE -ne 0) {
    throw "Release content audit failed."
}

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -LiteralPath $PortableRoot -DestinationPath $ZipPath -CompressionLevel Optimal

$MainExe = Join-Path $PortableRoot "MMH3PromptBuilder.exe"
$CpuServer = Join-Path $PortableRoot "_internal\runtime\cpu\llama-server.exe"
$VulkanServer = Join-Path $PortableRoot "_internal\runtime\vulkan\llama-server.exe"
$ZipHash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$ExeHash = (Get-FileHash -LiteralPath $MainExe -Algorithm SHA256).Hash.ToLowerInvariant()
$CpuServerHash = (Get-FileHash -LiteralPath $CpuServer -Algorithm SHA256).Hash.ToLowerInvariant()
$VulkanServerHash = (Get-FileHash -LiteralPath $VulkanServer -Algorithm SHA256).Hash.ToLowerInvariant()
$ChecksumLines = @(
    "{0}  {1}" -f $ZipHash, (Split-Path $ZipPath -Leaf)
    "{0}  {1}" -f $ExeHash, "$PortableName/MMH3PromptBuilder.exe"
    "{0}  {1}" -f $CpuServerHash, "$PortableName/_internal/runtime/cpu/llama-server.exe"
    "{0}  {1}" -f $VulkanServerHash, "$PortableName/_internal/runtime/vulkan/llama-server.exe"
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
$Manifest = Get-Content -LiteralPath $ManifestTemplatePath -Raw
$Manifest = $Manifest.Replace("{{APP_VERSION}}", $Version)
$Manifest = $Manifest.Replace("{{RELEASE_DATE}}", $ReleaseDate)
$Manifest = $Manifest.Replace("{{LLAMA_CPP_VERSION}}", $LlamaVersion)
$Manifest = $Manifest.Replace("{{LLAMA_CPP_COMMIT}}", $LlamaCommit)
$Manifest = $Manifest.Replace("{{CPU_ASSET_NAME}}", $CpuAssetName)
$Manifest = $Manifest.Replace("{{CPU_ASSET_SHA256}}", $CpuAssetSha256)
$Manifest = $Manifest.Replace("{{VULKAN_ASSET_NAME}}", $VulkanAssetName)
$Manifest = $Manifest.Replace("{{VULKAN_ASSET_SHA256}}", $VulkanAssetSha256)
$Manifest = $Manifest.Replace("{{EXE_SHA256}}", $ExeHash)
$Manifest = $Manifest.Replace("{{ZIP_SHA256}}", $ZipHash)
Set-Content -LiteralPath $ManifestPath -Value $Manifest -Encoding UTF8

Write-Host "Portable onedir: $PortableRoot"
Write-Host "Release ZIP: $ZipPath"
Write-Host "Checksums: $ChecksumsPath"
Write-Host "Manifest: $ManifestPath"
