param(
    [string]$Version = "",
    [ValidateSet("cpu", "vulkan")]
    [string]$Variant = "cpu",
    [string]$Sha256 = ""
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

$PinnedVersionFile = Resolve-WorkspaceChild (Join-Path $ProjectRoot "runtime\LLAMA_CPP_VERSION_PIN.txt")
$VariantUpper = $Variant.ToUpperInvariant()
$PinnedSha256File = Resolve-WorkspaceChild (
    (Join-Path $ProjectRoot "runtime\LLAMA_CPP_${VariantUpper}_SHA256_PIN.txt")
)
$PinnedCommitFile = Resolve-WorkspaceChild (Join-Path $ProjectRoot "runtime\LLAMA_CPP_COMMIT_PIN.txt")
$PinnedVersion = (Get-Content -LiteralPath $PinnedVersionFile -Raw).Trim()
if (-not $Version) {
    $Version = $PinnedVersion
}
if ((-not $Sha256) -and ($Version -eq $PinnedVersion) -and (Test-Path -LiteralPath $PinnedSha256File)) {
    $Sha256 = (Get-Content -LiteralPath $PinnedSha256File -Raw).Trim()
}

$AssetNames = @{
    cpu = "llama-$Version-bin-win-cpu-x64.zip"
    vulkan = "llama-$Version-bin-win-vulkan-x64.zip"
}
$AssetName = $AssetNames[$Variant]
$ReleaseApi = "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/$Version"
$Headers = @{ "User-Agent" = "MMH3-Prompt-Builder-runtime-fetch" }
Write-Host "Querying official llama.cpp release metadata: $ReleaseApi"
$Release = Invoke-RestMethod -Headers $Headers -Uri $ReleaseApi
if ($Release.tag_name -ne $Version) {
    throw "Official release tag identity mismatch. The runtime was not changed."
}
$MatchingAssets = @($Release.assets | Where-Object { $_.name -ceq $AssetName })
if ($MatchingAssets.Count -ne 1) {
    throw "The exact official asset was not found exactly once: $AssetName"
}
$Asset = $MatchingAssets[0]
$ExpectedUrl = "https://github.com/ggml-org/llama.cpp/releases/download/$Version/$AssetName"
if ($Asset.browser_download_url -cne $ExpectedUrl) {
    throw "Official asset URL identity mismatch. The runtime was not changed."
}
if (-not $Asset.digest -or -not $Asset.digest.StartsWith("sha256:")) {
    throw "The official GitHub asset SHA256 digest is unavailable. The runtime was not changed."
}
$OfficialSha256 = $Asset.digest.Substring(7).ToLowerInvariant()
if ($Sha256 -and ($Sha256.ToLowerInvariant() -ne $OfficialSha256)) {
    throw "Pinned SHA256 does not match the official GitHub release digest."
}
$Sha256 = $OfficialSha256

$PinnedCommit = ""
if (Test-Path -LiteralPath $PinnedCommitFile) {
    $PinnedCommit = (Get-Content -LiteralPath $PinnedCommitFile -Raw).Trim()
}
if ($PinnedCommit -and ($Version -eq $PinnedVersion) -and ($Release.target_commitish -cne $PinnedCommit)) {
    throw "Pinned commit does not match the official GitHub release metadata."
}

$Destination = Resolve-WorkspaceChild (Join-Path $ProjectRoot "runtime\$Variant")
$TempRoot = Resolve-WorkspaceChild (Join-Path $ProjectRoot ".tmp")
$TempDirectory = Resolve-WorkspaceChild (
    (Join-Path $TempRoot ("mmh3-llama-" + [guid]::NewGuid().ToString("N")))
)
New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
New-Item -ItemType Directory -Path $TempDirectory | Out-Null

try {
    $Archive = Resolve-WorkspaceChild (Join-Path $TempDirectory $AssetName)
    Write-Host "Downloading official llama.cpp asset: $ExpectedUrl"
    Invoke-WebRequest -Headers $Headers -Uri $ExpectedUrl -OutFile $Archive
    if ((Get-Item -LiteralPath $Archive).Length -ne [int64]$Asset.size) {
        throw "Downloaded archive size does not match official release metadata."
    }
    $Actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne $Sha256) {
        throw "SHA256 mismatch. The runtime was not changed."
    }

    $Extracted = Resolve-WorkspaceChild (Join-Path $TempDirectory "extracted")
    New-Item -ItemType Directory -Path $Extracted | Out-Null
    $ExtractedPrefix = $Extracted.TrimEnd($DirectorySeparator, $AltDirectorySeparator) + $DirectorySeparator
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Zip = [System.IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        foreach ($Entry in $Zip.Entries) {
            $EntryTarget = [System.IO.Path]::GetFullPath((Join-Path $Extracted $Entry.FullName))
            if (-not $EntryTarget.StartsWith(
                $ExtractedPrefix,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
                throw "Unsafe archive entry detected: $($Entry.FullName)"
            }
        }
    }
    finally {
        $Zip.Dispose()
    }
    Expand-Archive -LiteralPath $Archive -DestinationPath $Extracted

    $Server = Get-ChildItem -LiteralPath $Extracted -Recurse -Filter "llama-server.exe" |
        Select-Object -First 1
    if (-not $Server) {
        throw "llama-server.exe was not found in the official archive."
    }
    $StagedRuntime = Resolve-WorkspaceChild (Join-Path $TempDirectory "runtime-ready")
    New-Item -ItemType Directory -Path $StagedRuntime | Out-Null
    Copy-Item -Path (Join-Path $Server.Directory.FullName "*") -Destination $StagedRuntime -Recurse -Force
    if (-not (Test-Path -LiteralPath (Join-Path $StagedRuntime "llama-server.exe") -PathType Leaf)) {
        throw "Staged llama-server.exe validation failed. The runtime was not changed."
    }
    Set-Content -LiteralPath (Join-Path $StagedRuntime "LLAMA_CPP_VERSION.txt") -Value $Version -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $StagedRuntime "LLAMA_CPP_COMMIT.txt") -Value $Release.target_commitish -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $StagedRuntime "LLAMA_CPP_ASSET.txt") -Value $AssetName -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $StagedRuntime "LLAMA_CPP_ARCHIVE_SHA256.txt") -Value $Actual -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $StagedRuntime "LLAMA_CPP_SOURCE_URL.txt") -Value $ExpectedUrl -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $StagedRuntime ".gitkeep") -Value "" -Encoding UTF8

    $Backup = Resolve-WorkspaceChild "$Destination.backup"
    if (Test-Path -LiteralPath $Backup) {
        Remove-Item -LiteralPath $Backup -Recurse -Force
    }
    if (Test-Path -LiteralPath $Destination) {
        Move-Item -LiteralPath $Destination -Destination $Backup
    }
    try {
        Move-Item -LiteralPath $StagedRuntime -Destination $Destination
    }
    catch {
        if ((Test-Path -LiteralPath $Backup) -and (-not (Test-Path -LiteralPath $Destination))) {
            Move-Item -LiteralPath $Backup -Destination $Destination
        }
        throw
    }
    if (Test-Path -LiteralPath $Backup) {
        Remove-Item -LiteralPath $Backup -Recurse -Force
    }
    Write-Host "Download completed: $Destination"
    Write-Host "Version: $Version"
    Write-Host "Commit: $($Release.target_commitish)"
    Write-Host "Asset: $AssetName"
    Write-Host "SHA256: $Actual"
}
finally {
    if (Test-Path -LiteralPath $TempDirectory) {
        Remove-Item -LiteralPath $TempDirectory -Recurse -Force
    }
}
