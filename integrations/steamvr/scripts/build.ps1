[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$OpenVrSdkRoot,
    [Parameter(Mandatory = $true)][string]$AssetDirectory,
    [ValidateSet("Debug", "Release")][string]$Configuration = "Release"
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) { $PSNativeCommandUseErrorActionPreference = $false }
if (-not [System.Environment]::Is64BitOperatingSystem -or $env:OS -ne "Windows_NT") {
    throw "The native SteamVR integration is built and tested on Windows x64 only."
}
$sourceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$sdkRoot = (Resolve-Path -LiteralPath $OpenVrSdkRoot).Path
$assetRoot = (Resolve-Path -LiteralPath $AssetDirectory).Path
. (Join-Path $PSScriptRoot "assets.ps1")
$null = Read-MikoTypeAssetManifest $assetRoot
if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) { throw "Install CMake 3.21+ and Visual Studio 2022 Desktop development with C++." }
$buildRoot = Join-Path $sourceRoot ".build"
$bundleRoot = Join-Path $sourceRoot "dist"
& cmake -S $sourceRoot -B $buildRoot -G "Visual Studio 17 2022" -A x64 "-DOPENVR_SDK_ROOT=$sdkRoot" "-DMIKOTYPE_ASSET_ROOT=$assetRoot"
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed ($LASTEXITCODE)." }
& cmake --build $buildRoot --config $Configuration
if ($LASTEXITCODE -ne 0) { throw "CMake build failed ($LASTEXITCODE)." }
& cmake --install $buildRoot --config $Configuration --prefix $bundleRoot
if ($LASTEXITCODE -ne 0) { throw "CMake install failed ($LASTEXITCODE)." }
Write-Host "Built native bridge and driver at $bundleRoot"
Write-Host "Existing files were not deleted. Stop SteamVR before rebuilding an installed driver."
Write-Host "Next: install.ps1, restart SteamVR, enable mikotypekeyboard, then run.ps1."
