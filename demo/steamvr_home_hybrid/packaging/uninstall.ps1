[CmdletBinding()]
param(
    [string]$SteamVrRoot = "",
    [string]$BundleRoot = (Join-Path $PSScriptRoot "dist")
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
$driverRoot = (Resolve-Path -LiteralPath (Join-Path $BundleRoot $driverName)).Path
$vrpathreg = Join-Path $SteamVrRoot "bin\win64\vrpathreg.exe"
if (-not (Test-Path -LiteralPath $vrpathreg -PathType Leaf)) {
    throw "SteamVR vrpathreg.exe was not found: $vrpathreg"
}

$findOutput = & $vrpathreg finddriver $driverName 2>&1
$findExit = $LASTEXITCODE
switch ($findExit) {
    0 {
        $registeredRoot = Driver-RootFromFindOutput -OutputLines $findOutput
        if ((Normalized-Path $registeredRoot) -ne (Normalized-Path $driverRoot)) {
            throw "Refusing to remove '$registeredRoot': this uninstaller only owns exact path '$driverRoot'."
        }
        & $vrpathreg removedriver $driverRoot
        if ($LASTEXITCODE -ne 0) {
            throw "vrpathreg removedriver failed with exit code $LASTEXITCODE."
        }
        Write-Host "Removed exact driver registration: $driverRoot"
    }
    1 {
        Write-Host "Driver '$driverName' is not registered; nothing was removed."
    }
    2 {
        throw "SteamVR reports multiple '$driverName' registrations. Refusing broad removal; remove each exact path manually."
    }
    default {
        throw "vrpathreg finddriver failed with exit code $findExit: $($findOutput -join [Environment]::NewLine)"
    }
}

Write-Host "Restart SteamVR to unload the driver. Bundle files were left intact."
