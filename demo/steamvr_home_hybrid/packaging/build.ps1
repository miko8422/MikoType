[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OpenVrSdkRoot,
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE: $Command $($Arguments -join ' ')"
    }
}

$sdkRoot = (Resolve-Path -LiteralPath $OpenVrSdkRoot).Path
$demoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$BuildRoot = Join-Path $PSScriptRoot ".build"
$OutputRoot = Join-Path $PSScriptRoot "dist"
$driverSource = Join-Path $demoRoot "windows_driver"
$overlaySource = Join-Path $demoRoot "windows_overlay"
$driverPackage = Join-Path $driverSource "deskvisionkeyboard"
$renderModel = Join-Path $driverPackage "resources\rendermodels\deskvision_keyboard"
$overlayPng = Join-Path $overlaySource "assets\keyboard_highlight_test.png"
$exportManifest = Join-Path $demoRoot "asset_export\export_manifest.json"

$requiredSdkFiles = @(
    (Join-Path $sdkRoot "headers\openvr.h"),
    (Join-Path $sdkRoot "headers\openvr_driver.h"),
    (Join-Path $sdkRoot "lib\win64\openvr_api.lib"),
    (Join-Path $sdkRoot "bin\win64\openvr_api.dll")
)
$requiredAssets = @(
    (Join-Path $driverPackage "driver.vrdrivermanifest"),
    (Join-Path $renderModel "deskvision_keyboard.json"),
    $exportManifest,
    $overlayPng
)

foreach ($path in $requiredSdkFiles + $requiredAssets) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required build input is missing: $path"
    }
}

foreach ($pattern in @("*.obj", "*.mtl", "*.png")) {
    if (-not (Get-ChildItem -LiteralPath $renderModel -Filter $pattern -File)) {
        throw "Render-model package contains no '$pattern' asset: $renderModel"
    }
}

$assetManifest = Get-Content -LiteralPath $exportManifest -Raw | ConvertFrom-Json
if ($assetManifest.schema_version -cne "steamvr-home-openvr-asset-export-0.1" -or
    $assetManifest.render_model.name -cne "deskvision_keyboard" -or
    $assetManifest.render_model.filename -cne "deskvision_keyboard.json") {
    throw "export_manifest.json does not match the DeskVision Render Model contract."
}
$assetRecords = @($assetManifest.outputs.PSObject.Properties)
if ($assetRecords.Count -lt 1) {
    throw "export_manifest.json contains no hashed outputs."
}
foreach ($property in $assetRecords) {
    $assetName = [string]$property.Name
    if ([System.IO.Path]::GetFileName($assetName) -cne $assetName) {
        throw "Export manifest contains an unsafe asset name: $assetName"
    }
    $assetPath = Join-Path $renderModel $assetName
    if (-not (Test-Path -LiteralPath $assetPath -PathType Leaf)) {
        throw "Export manifest asset is missing from the Render Model: $assetPath"
    }
    $actualLength = (Get-Item -LiteralPath $assetPath).Length
    if ($actualLength -ne [long]$property.Value.byte_length) {
        throw "Exported asset length mismatch: $assetName"
    }
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $assetPath).Hash.ToLowerInvariant()
    if ($actualHash -cne ([string]$property.Value.sha256).ToLowerInvariant()) {
        throw "Exported asset SHA-256 mismatch: $assetName"
    }
}

$manifestPath = Join-Path $driverPackage "driver.vrdrivermanifest"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.name -cne "deskvisionkeyboard" -or
    $manifest.directory -cne "" -or
    $manifest.alwaysActivate -ne $true -or
    $manifest.resourceOnly -ne $false -or
    $null -eq $manifest.PSObject.Properties["hmd_presence"] -or
    @($manifest.hmd_presence).Count -ne 0) {
    throw "driver.vrdrivermanifest does not match the DeskVision package contract."
}

foreach ($exactDirectory in @($BuildRoot, $OutputRoot)) {
    if (Test-Path -LiteralPath $exactDirectory) {
        Remove-Item -LiteralPath $exactDirectory -Recurse -Force
    }
    New-Item -ItemType Directory -Path $exactDirectory | Out-Null
}

$driverBuild = Join-Path $BuildRoot "driver"
$overlayBuild = Join-Path $BuildRoot "overlay"

Invoke-Checked -Command "cmake" -Arguments @(
    "-S", $driverSource,
    "-B", $driverBuild,
    "-G", "Visual Studio 17 2022",
    "-A", "x64",
    "-DOPENVR_SDK_ROOT=$sdkRoot"
)
Invoke-Checked -Command "cmake" -Arguments @(
    "--build", $driverBuild,
    "--config", $Configuration
)
Invoke-Checked -Command "cmake" -Arguments @(
    "--install", $driverBuild,
    "--config", $Configuration,
    "--prefix", $OutputRoot
)

Invoke-Checked -Command "cmake" -Arguments @(
    "-S", $overlaySource,
    "-B", $overlayBuild,
    "-G", "Visual Studio 17 2022",
    "-A", "x64",
    "-DOPENVR_SDK_ROOT=$sdkRoot"
)
Invoke-Checked -Command "cmake" -Arguments @(
    "--build", $overlayBuild,
    "--config", $Configuration
)
Invoke-Checked -Command "cmake" -Arguments @(
    "--install", $overlayBuild,
    "--config", $Configuration,
    "--prefix", $OutputRoot
)

$builtDriver = Join-Path $OutputRoot "deskvisionkeyboard\bin\win64\driver_deskvisionkeyboard.dll"
$builtOverlay = Join-Path $OutputRoot "overlay\deskvision_keyboard_overlay.exe"
$builtOpenVr = Join-Path $OutputRoot "overlay\openvr_api.dll"
$builtPng = Join-Path $OutputRoot "overlay\keyboard_highlight_test.png"
foreach ($path in @($builtDriver, $builtOverlay, $builtOpenVr, $builtPng)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Build completed without required package output: $path"
    }
}

Write-Host "DeskVision SteamVR smoke bundle built at: $OutputRoot"
Get-FileHash -Algorithm SHA256 -LiteralPath $builtDriver, $builtOverlay, $builtPng |
    Format-Table -AutoSize
