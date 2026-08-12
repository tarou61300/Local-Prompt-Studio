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
$PortableName = "Local-Prompt-Studio-v$Version-win-x64-portable"
if (-not $ZipPath) {
    $ZipPath = Join-Path $ProjectRoot "release\$PortableName.zip"
}
$ZipPath = Resolve-WorkspaceChild $ZipPath
if (-not (Test-Path -LiteralPath $ZipPath -PathType Leaf)) {
    throw "Release ZIP was not found: $ZipPath"
}
$JapanesePathSegment = -join @(
    [char]0x65E5,
    [char]0x672C,
    [char]0x8A9E,
    " ",
    [char]0x30D1,
    [char]0x30B9
)
$VerificationId = [guid]::NewGuid().ToString("N").Substring(0, 8)
$VerificationRoot = Resolve-WorkspaceChild (
    Join-Path $ProjectRoot (
        ".tmp\v\$JapanesePathSegment $VerificationId"
    )
)
$ExternalSentinelRoot = Resolve-WorkspaceChild (
    Join-Path $ProjectRoot (
        ".tmp\x\$VerificationId"
    )
)
$BaselineServerIds = @(
    Get-Process -Name "llama-server" -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Id
)
New-Item -ItemType Directory -Path $VerificationRoot -Force | Out-Null
try {
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $VerificationRoot
    $DistributionRoot = Resolve-WorkspaceChild (Join-Path $VerificationRoot $PortableName)
    $NestedApplicationRoot = Join-Path $DistributionRoot "LocalPromptStudio"
    $PortableRoot = if (Test-Path -LiteralPath $NestedApplicationRoot -PathType Container) {
        Resolve-WorkspaceChild $NestedApplicationRoot
    }
    else {
        $DistributionRoot
    }
    $Python = Resolve-WorkspaceChild (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
    & $Python (Join-Path $ProjectRoot "scripts\audit_release.py") $DistributionRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Expanded release audit failed."
    }

    $LocalAppDataSentinel = Resolve-WorkspaceChild (Join-Path $ExternalSentinelRoot "localappdata-sentinel")
    $AppDataSentinel = Resolve-WorkspaceChild (Join-Path $ExternalSentinelRoot "appdata-sentinel")
    $ProgramDataSentinel = Resolve-WorkspaceChild (Join-Path $ExternalSentinelRoot "programdata-sentinel")
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
        $Application = Join-Path $PortableRoot "LocalPromptStudio.exe"
        $VersionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($Application)
        if ($VersionInfo.ProductVersion -ne $Version) {
            throw "Packaged ProductVersion is '$($VersionInfo.ProductVersion)', expected '$Version'."
        }
        $NumericVersion = (($Version -split '-', 2)[0] -split '\.') + @("0")
        $ExpectedFileVersion = ($NumericVersion[0..3] -join '.')
        if ($VersionInfo.FileVersion -ne $ExpectedFileVersion) {
            throw "Packaged FileVersion is '$($VersionInfo.FileVersion)', expected '$ExpectedFileVersion'."
        }
        $Process = Start-Process -FilePath $Application `
            -ArgumentList "--skip-setup" `
            -WorkingDirectory $PortableRoot `
            -PassThru
        $SettingsInvoker = $null
        try {
            $WindowDeadline = (Get-Date).AddSeconds(15)
            do {
                Start-Sleep -Milliseconds 200
                $Process.Refresh()
            }
            while (
                -not $Process.HasExited -and
                [string]::IsNullOrEmpty($Process.MainWindowTitle) -and
                (Get-Date) -lt $WindowDeadline
            )
            if ($Process.HasExited) {
                throw "Portable application exited before showing its MainWindow."
            }
            if ($Process.MainWindowTitle -ne "Local Prompt Studio v$Version") {
                throw "Packaged MainWindow title is '$($Process.MainWindowTitle)'."
            }

            $AutomationHelper = Resolve-WorkspaceChild (
                Join-Path $VerificationRoot "invoke-settings.ps1"
            )
            $AutomationScript = @'
param([int]$TargetProcessId)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$settingsName = -join ([char]0x8A2D, [char]0x5B9A)
$targetProcess = Get-Process -Id $TargetProcessId
$targetProcess.Refresh()
$mainWindow = [System.Windows.Automation.AutomationElement]::FromHandle(
    $targetProcess.MainWindowHandle
)
$nameCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty,
    $settingsName
)
$button = $mainWindow.FindFirst(
    [System.Windows.Automation.TreeScope]::Descendants,
    $nameCondition
)
if ($null -eq $button) {
    throw "Settings button was not found."
}
$invoke = $button.GetCurrentPattern(
    [System.Windows.Automation.InvokePattern]::Pattern
)
$invoke.Invoke()
'@
            Set-Content -LiteralPath $AutomationHelper -Value $AutomationScript -Encoding ASCII
            $QuotedAutomationHelper = '"' + $AutomationHelper + '"'
            $SettingsInvoker = Start-Process -FilePath "powershell.exe" `
                -ArgumentList @(
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    $QuotedAutomationHelper,
                    $Process.Id
                ) `
                -WindowStyle Hidden `
                -PassThru

            Add-Type -AssemblyName UIAutomationClient
            Add-Type -AssemblyName UIAutomationTypes
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class ReleaseWindowSearch
{
    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);

    public static IntPtr FindByProcessAndTitle(int processId, string title)
    {
        IntPtr result = IntPtr.Zero;
        EnumWindows(delegate (IntPtr handle, IntPtr state)
        {
            uint ownerProcessId;
            GetWindowThreadProcessId(handle, out ownerProcessId);
            if (ownerProcessId != (uint)processId)
            {
                return true;
            }
            StringBuilder text = new StringBuilder(512);
            GetWindowText(handle, text, text.Capacity);
            if (String.Equals(text.ToString(), title, StringComparison.Ordinal))
            {
                result = handle;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return result;
    }
}
'@
            $SettingsName = -join ([char]0x8A2D, [char]0x5B9A)
            $SettingsHandle = [IntPtr]::Zero
            $SettingsDeadline = (Get-Date).AddSeconds(20)
            do {
                Start-Sleep -Milliseconds 250
                $Process.Refresh()
                $SettingsHandle = [ReleaseWindowSearch]::FindByProcessAndTitle(
                    $Process.Id,
                    $SettingsName
                )
            }
            while (
                $SettingsHandle -eq [IntPtr]::Zero -and
                -not $Process.HasExited -and
                (Get-Date) -lt $SettingsDeadline
            )
            if ($Process.HasExited -or $SettingsHandle -eq [IntPtr]::Zero) {
                throw "Packaged Settings dialog did not open."
            }
            $SettingsWindow = [System.Windows.Automation.AutomationElement]::FromHandle(
                $SettingsHandle
            )
            if ($null -eq $SettingsWindow) {
                throw "Packaged Settings UI Automation element was not found."
            }
            $SettingsWindow.GetCurrentPattern(
                [System.Windows.Automation.WindowPattern]::Pattern
            ).Close()
            if (-not $SettingsInvoker.WaitForExit(10000) -or $SettingsInvoker.ExitCode -ne 0) {
                throw "Settings UI Automation helper did not finish cleanly."
            }
            if (-not $Process.CloseMainWindow() -or -not $Process.WaitForExit(5000)) {
                throw "Packaged MainWindow did not close cleanly."
            }
            if ($Process.ExitCode -ne 0) {
                throw "Portable application smoke test failed with exit code $($Process.ExitCode)."
            }
        }
        finally {
            if ($null -ne $SettingsInvoker -and -not $SettingsInvoker.HasExited) {
                Stop-Process -Id $SettingsInvoker.Id -Force -ErrorAction SilentlyContinue
            }
            if (-not $Process.HasExited) {
                Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
            }
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
        foreach ($Relative in @(
            "config.json",
            "history.sqlite3",
            "comfyui_credentials.dat",
            "skills"
        )) {
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

    $PortableLog = Join-Path $PortableRoot "data\local-prompt-studio.log"
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
    Write-Host "Expanded distribution smoke test passed: $DistributionRoot"
    Write-Host "Packaged application verified: $PortableRoot"
}
finally {
    if (Test-Path -LiteralPath $VerificationRoot) {
        Remove-Item -LiteralPath $VerificationRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $ExternalSentinelRoot) {
        Remove-Item -LiteralPath $ExternalSentinelRoot -Recurse -Force
    }
}
