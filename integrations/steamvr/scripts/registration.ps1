# Shared path checks; dot-sourced only by install/uninstall scripts.
function Normalize-MikoTypePath([string]$Path) {
    return [IO.Path]::GetFullPath($Path.Trim().Trim('"')).TrimEnd('\', '/')
}
function Get-MikoTypeRegisteredRoot([object[]]$Lines) {
    $last = ($Lines | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ } | Select-Object -Last 1)
    if ([string]::IsNullOrWhiteSpace($last)) { throw "vrpathreg returned an empty registered path." }
    $value = $last.Trim('"')
    if ([IO.Path]::GetExtension($value) -ieq ".vrdrivermanifest") { return Split-Path -Parent $value }
    return $value
}
function Resolve-MikoTypeRegistration([string]$SteamVrRoot, [string]$BundleRoot) {
    if ([string]::IsNullOrWhiteSpace($SteamVrRoot)) {
        $SteamVrRoot = Join-Path ${env:ProgramFiles(x86)} "Steam\steamapps\common\SteamVR"
    }
    $tool = Join-Path $SteamVrRoot "bin\win64\vrpathreg.exe"
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw "SteamVR vrpathreg.exe missing; pass -SteamVrRoot for your Steam library." }
    $driverRoot = (Resolve-Path -LiteralPath (Join-Path $BundleRoot "mikotypekeyboard")).Path
    foreach ($file in @("driver.vrdrivermanifest", "bin\win64\driver_mikotypekeyboard.dll")) {
        if (-not (Test-Path -LiteralPath (Join-Path $driverRoot $file) -PathType Leaf)) { throw "Build the production driver first ($file missing)." }
    }
    $manifest = Get-Content -LiteralPath (Join-Path $driverRoot "driver.vrdrivermanifest") -Raw | ConvertFrom-Json
    if ($manifest.name -ne "mikotypekeyboard") { throw "Refusing registration of an unrelated driver." }
    return @{ tool = $tool; driver = $driverRoot }
}
