[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AssetDirectory,
    [string]$BundleRoot = (Join-Path $PSScriptRoot "..\dist")
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if (Get-Process -Name vrserver -ErrorAction SilentlyContinue) {
    throw "Close SteamVR before replacing keyboard assets. No process was stopped."
}
. (Join-Path $PSScriptRoot "assets.ps1")
$assetRoot = (Resolve-Path -LiteralPath $AssetDirectory).Path
$root = (Resolve-Path -LiteralPath $BundleRoot).Path
$manifest = Read-MikoTypeAssetManifest $assetRoot
$driver = Join-Path $root "mikotypekeyboard"
if (-not (Test-Path -LiteralPath (Join-Path $driver "bin\win64\driver_mikotypekeyboard.dll") -PathType Leaf)) {
    throw "Select a compiled/downloaded MikoType bundle, not an arbitrary output directory."
}
$destination = Join-Path $driver "resources\rendermodels\mikotype_keyboard"
$null = New-Item -ItemType Directory -Path $destination -Force
foreach ($property in $manifest.outputs.PSObject.Properties) {
    Copy-Item -LiteralPath (Join-Path $assetRoot $property.Name) -Destination (Join-Path $destination $property.Name) -Force
}
# Publish the fingerprint last. A failed copy cannot silently bless a partial
# new model as the installed revision. No compiled DLL or unrelated file moves.
Copy-Item -LiteralPath (Join-Path $assetRoot "export_manifest.json") -Destination (Join-Path $destination "export_manifest.json") -Force
Copy-Item -LiteralPath (Join-Path $assetRoot "export_manifest.json") -Destination (Join-Path $root "export_manifest.json") -Force
Write-Host "Updated validated keyboard assets and fingerprint only. Native rebuild was not needed."
Write-Host "Restart SteamVR and the MikoType bridge. Old unreferenced material files were retained."
