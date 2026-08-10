param(
    [ValidateSet("cpu", "vulkan")]
    [string]$Variant = "cpu",
    [ValidateRange(3, 60)]
    [int]$TimeoutSeconds = 12
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$DirectorySeparator = [System.IO.Path]::DirectorySeparatorChar
$AltDirectorySeparator = [System.IO.Path]::AltDirectorySeparatorChar
$WorkspacePrefix = $ProjectRoot.TrimEnd($DirectorySeparator, $AltDirectorySeparator) + $DirectorySeparator

function Resolve-WorkspaceChild {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $Resolved = [System.IO.Path]::GetFullPath($Path)
    $OutsideByPrefix = -not $Resolved.StartsWith(
        $WorkspacePrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
    if ($OutsideByPrefix) {
        throw "Path is outside the project workspace: $Resolved"
    }
    return $Resolved
}

$SanityDirectory = Resolve-WorkspaceChild (Join-Path $ProjectRoot ".tmp\llama-sanity\$Variant")
$RuntimeDirectory = Resolve-WorkspaceChild (Join-Path $ProjectRoot "runtime\$Variant")
$Executable = Resolve-WorkspaceChild (Join-Path $RuntimeDirectory "llama-server.exe")
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "CPU llama-server.exe was not found: $Executable"
}

New-Item -ItemType Directory -Path $SanityDirectory -Force | Out-Null
$RunId = [guid]::NewGuid().ToString("N")
$StdoutLog = Resolve-WorkspaceChild (Join-Path $SanityDirectory "$RunId.stdout.log")
$StderrLog = Resolve-WorkspaceChild (Join-Path $SanityDirectory "$RunId.stderr.log")

$Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$Listener.Start()
$Port = ([System.Net.IPEndPoint]$Listener.LocalEndpoint).Port
$Listener.Stop()

Write-Output "PROJECT_ROOT=$ProjectRoot"
Write-Output "WORKSPACE_PREFIX=$WorkspacePrefix"
Write-Output "SANITY_DIRECTORY=$SanityDirectory"
Write-Output "EXECUTABLE=$Executable"
Write-Output "BACKEND=$Variant"
Write-Output "BOUND_ADDRESS=127.0.0.1"
Write-Output "PORT=$Port"

$StartInfo = New-Object System.Diagnostics.ProcessStartInfo
$StartInfo.FileName = $Executable
$StartInfo.Arguments = "--host 127.0.0.1 --port $Port --no-webui"
$StartInfo.WorkingDirectory = $RuntimeDirectory
$StartInfo.UseShellExecute = $false
$StartInfo.CreateNoWindow = $true
$StartInfo.RedirectStandardOutput = $true
$StartInfo.RedirectStandardError = $true
$Process = New-Object System.Diagnostics.Process
$Process.StartInfo = $StartInfo
if (-not $Process.Start()) {
    throw "Failed to start llama-server.exe."
}
$StdoutTask = $Process.StandardOutput.ReadToEndAsync()
$StderrTask = $Process.StandardError.ReadToEndAsync()

$HealthResponse = $null
$StartedAt = [DateTime]::UtcNow
try {
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        $Process.Refresh()
        if ($Process.HasExited) {
            break
        }
        try {
            $Response = Invoke-WebRequest `
                -UseBasicParsing `
                -Uri "http://127.0.0.1:$Port/health" `
                -TimeoutSec 1
            if ($Response.StatusCode -eq 200) {
                $HealthResponse = $Response.Content
                break
            }
        }
        catch {
            # Expected while the local server is starting.
        }
        Start-Sleep -Milliseconds 200
    }

    $Process.Refresh()
    Write-Output "PID=$($Process.Id)"
    Write-Output "EXITED_BEFORE_CLEANUP=$($Process.HasExited)"
    if ($HealthResponse) {
        Write-Output "HEALTH_STATUS=200"
        Write-Output "HEALTH_BODY=$HealthResponse"
    }
    else {
        Write-Output "HEALTH_STATUS=unavailable"
    }
}
finally {
    $Process.Refresh()
    if (-not $Process.HasExited) {
        $Process.Kill()
        $Process.WaitForExit()
    }
    $StdoutText = $StdoutTask.GetAwaiter().GetResult()
    $StderrText = $StderrTask.GetAwaiter().GetResult()
    [System.IO.File]::WriteAllText($StdoutLog, $StdoutText, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText($StderrLog, $StderrText, [System.Text.UTF8Encoding]::new($false))
    $Process.Refresh()
    Write-Output "FINAL_EXIT_CODE=$($Process.ExitCode)"
    Write-Output "RUNTIME_MS=$([int]([DateTime]::UtcNow - $StartedAt).TotalMilliseconds)"
    Write-Output "STDOUT_LOG=$StdoutLog"
    Write-Output "STDERR_LOG=$StderrLog"
}

Write-Output "--- STDERR ---"
if (Test-Path -LiteralPath $StderrLog) {
    Get-Content -LiteralPath $StderrLog -Tail 100
}
Write-Output "--- STDOUT ---"
if (Test-Path -LiteralPath $StdoutLog) {
    Get-Content -LiteralPath $StdoutLog -Tail 40
}
