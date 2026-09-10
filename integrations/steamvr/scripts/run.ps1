[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ServiceUrl,
    [Parameter(Mandatory = $true)][string]$TokenFile,
    [string]$BundleRoot = (Join-Path $PSScriptRoot "..\dist")
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) { $PSNativeCommandUseErrorActionPreference = $false }
if ($ServiceUrl -notmatch '^http://127\.0\.0\.1:[0-9]{1,5}/?$') { throw "Use the actual local URL printed by MikoType, such as http://127.0.0.1:9000." }
$root = (Resolve-Path -LiteralPath $BundleRoot).Path
$tokenPath = (Resolve-Path -LiteralPath $TokenFile).Path
$manifest = Get-Content -LiteralPath (Join-Path $root "export_manifest.json") -Raw | ConvertFrom-Json
$sha = $manifest.source.glb_sha256
if ($sha -notmatch '^[0-9a-fA-F]{64}$') { throw "Build has no valid model fingerprint; rebuild from current WebUI assets." }
$bridge = Join-Path $root "bridge\mikotype_steamvr_bridge.exe"
if (-not (Test-Path -LiteralPath $bridge -PathType Leaf)) { throw "Build the Windows bridge first." }
Write-Host "Starting local-only SteamVR bridge. Keep the MikoType WebUI /steamvr page open for status/logs."
Write-Host "The token is read from a file and will not be printed. Ctrl+C stops only this bridge."
& $bridge --url $ServiceUrl --token-file $tokenPath --model-sha256 $sha
if ($LASTEXITCODE -ne 0) { throw "MikoType bridge exited with code $LASTEXITCODE. Check console and WebUI SteamVR diagnostics." }
