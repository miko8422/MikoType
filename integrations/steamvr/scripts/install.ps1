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
if ($result -eq 0) {
    $existing = Get-MikoTypeRegisteredRoot $output
    if ((Normalize-MikoTypePath $existing) -ne (Normalize-MikoTypePath $target.driver)) {
        throw "mikotypekeyboard already belongs to another path. Uninstall that exact registration first: $existing"
    }
} elseif ($result -eq 1) {
    & $target.tool adddriver $target.driver
    if ($LASTEXITCODE -ne 0) { throw "vrpathreg adddriver failed ($LASTEXITCODE)." }
} else { throw "vrpathreg finddriver failed or found duplicates ($result). No registrations were changed." }
$verified = & $target.tool finddriver mikotypekeyboard 2>&1
if ($LASTEXITCODE -ne 0 -or (Normalize-MikoTypePath (Get-MikoTypeRegisteredRoot $verified)) -ne (Normalize-MikoTypePath $target.driver)) {
    throw "Driver registration verification failed."
}
Write-Host "Registered exact MikoType driver path: $($target.driver)"
Write-Host "Restart SteamVR and enable mikotypekeyboard in Settings > Startup/Shutdown > Manage Add-ons."
Write-Host "No Pimax/VPN settings were changed."
