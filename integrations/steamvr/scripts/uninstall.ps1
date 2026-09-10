[CmdletBinding()]
param(
    [string]$SteamVrRoot = "",
    [string]$BundleRoot = (Join-Path $PSScriptRoot "..\dist")
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) { $PSNativeCommandUseErrorActionPreference = $false }
. (Join-Path $PSScriptRoot "registration.ps1")
$target = Resolve-MikoTypeRegistration $SteamVrRoot $BundleRoot
$output = & $target.tool finddriver mikotypekeyboard 2>&1
$result = $LASTEXITCODE
if ($result -eq 1) { Write-Host "MikoType driver is not registered; nothing changed."; return }
if ($result -ne 0) { throw "Ambiguous/failed registration lookup ($result); nothing was removed." }
$existing = Get-MikoTypeRegisteredRoot $output
if ((Normalize-MikoTypePath $existing) -ne (Normalize-MikoTypePath $target.driver)) {
    throw "Refusing to remove a registration outside this exact build: $existing"
}
& $target.tool removedriver $target.driver
if ($LASTEXITCODE -ne 0) { throw "vrpathreg removedriver failed ($LASTEXITCODE)." }
Write-Host "Removed only this MikoType driver registration. Files were retained; restart SteamVR."
