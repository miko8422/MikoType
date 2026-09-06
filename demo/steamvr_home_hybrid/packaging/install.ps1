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
$manifest = Join-Path $driverRoot "driver.vrdrivermanifest"
$driverDll = Join-Path $driverRoot "bin\win64\driver_deskvisionkeyboard.dll"

foreach ($path in @($vrpathreg, $manifest, $driverDll)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required install input is missing: $path"
    }
}

# Valve requires finddriver before adddriver so duplicate registrations never
# become an ambiguous runtime choice.
$findOutput = & $vrpathreg finddriver $driverName 2>&1
$findExit = $LASTEXITCODE
switch ($findExit) {
    0 {
        $registeredRoot = Driver-RootFromFindOutput -OutputLines $findOutput
        if ((Normalized-Path $registeredRoot) -ne (Normalized-Path $driverRoot)) {
            throw "Driver name '$driverName' is already registered at '$registeredRoot'. Uninstall that exact path before installing '$driverRoot'."
        }
        Write-Host "Driver is already registered at the requested path: $driverRoot"
    }
    1 {
        & $vrpathreg adddriver $driverRoot
        if ($LASTEXITCODE -ne 0) {
            throw "vrpathreg adddriver failed with exit code $LASTEXITCODE."
        }
    }
    2 {
        throw "SteamVR reports multiple registrations named '$driverName'. Resolve them manually before installing."
    }
    default {
        throw "vrpathreg finddriver failed with exit code $findExit: $($findOutput -join [Environment]::NewLine)"
    }
}

$verifyOutput = & $vrpathreg finddriver $driverName 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Post-install finddriver verification failed with exit code $LASTEXITCODE."
}
$verifiedRoot = Driver-RootFromFindOutput -OutputLines $verifyOutput
if ((Normalized-Path $verifiedRoot) -ne (Normalized-Path $driverRoot)) {
    throw "Post-install registration points to '$verifiedRoot', not '$driverRoot'."
}

Write-Host "Registered exact driver root: $driverRoot"
Write-Host "Restart SteamVR, enable 'deskvisionkeyboard' under Manage Add-ons, then run smoke.ps1."
