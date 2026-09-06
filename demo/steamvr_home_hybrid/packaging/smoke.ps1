[CmdletBinding()]
param(
    [string]$SteamVrRoot = "",
    [string]$BundleRoot = (Join-Path $PSScriptRoot "dist"),
    [ValidateRange(1, 300)]
    [int]$DurationSeconds = 8
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Normalized-Path {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path.Trim().Trim('"')).TrimEnd('\', '/')
}

function Driver-RootFromFindOutput {
    param([Parameter(Mandatory = $true)][object[]]$OutputLines)
    $line = ($OutputLines | ForEach-Object { $_.ToString().Trim() } |
        Where-Object { $_ -ne "" } | Select-Object -Last 1)
    if ([string]::IsNullOrWhiteSpace($line)) {
        throw "vrpathreg finddriver returned success without a path."
    }
    $path = $line.Trim('"')
    if ([System.IO.Path]::GetExtension($path) -ieq ".vrdrivermanifest") {
        return Split-Path -Parent $path
    }
    return $path
}

if ([string]::IsNullOrWhiteSpace($SteamVrRoot)) {
    $SteamVrRoot = Join-Path ${env:ProgramFiles(x86)} "Steam\steamapps\common\SteamVR"
}

$driverName = "deskvisionkeyboard"
$vrpathreg = Join-Path $SteamVrRoot "bin\win64\vrpathreg.exe"
$expectedDriverRoot = (Resolve-Path -LiteralPath (Join-Path $BundleRoot $driverName)).Path
$overlayExe = Join-Path $BundleRoot "overlay\deskvision_keyboard_overlay.exe"
$highlightPng = Join-Path $BundleRoot "overlay\keyboard_highlight_test.png"

foreach ($path in @($vrpathreg, $overlayExe, $highlightPng)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required smoke-test file is missing: $path"
    }
}

$findOutput = & $vrpathreg finddriver $driverName 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Driver '$driverName' is not uniquely registered. Run install.ps1 first. finddriver output: $($findOutput -join [Environment]::NewLine)"
}
$registeredRoot = Driver-RootFromFindOutput -OutputLines $findOutput
if ((Normalized-Path $registeredRoot) -ne (Normalized-Path $expectedDriverRoot)) {
    throw "Registered driver path '$registeredRoot' is not this smoke bundle '$expectedDriverRoot'. Run install.ps1 for the current dist first."
}

if (-not (Get-Process -Name "vrserver" -ErrorAction SilentlyContinue)) {
    throw "SteamVR vrserver is not running. Start SteamVR after installing the driver, then retry."
}

Write-Host "Registered driver: $($findOutput -join [Environment]::NewLine)"
Write-Host "Binding static highlight overlay for $DurationSeconds seconds..."
& $overlayExe $highlightPng $DurationSeconds
if ($LASTEXITCODE -ne 0) {
    throw "Overlay smoke process failed with exit code $LASTEXITCODE."
}

Write-Host "Smoke process passed. Visually confirm both keyboard model and highlight in the headset."
Write-Host "Run '$vrpathreg show' and use its 'Log Path' value; SteamVR logs are not necessarily under the runtime directory."
Write-Host "Inspect <Log Path>\vrserver.txt for Driver/RenderModel errors."
Write-Host "Inspect <Log Path>\vrclient_vrcompositor.txt for Render Model client parse errors."
Write-Host "Inspect <Log Path>\vrcompositor.txt for compositor/Overlay errors."
