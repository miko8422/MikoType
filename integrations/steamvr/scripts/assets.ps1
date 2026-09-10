# Dot-sourced exact asset validation, shared by build and prebuilt-bundle update.
function Read-MikoTypeAssetManifest([string]$AssetRoot) {
    $manifest = Get-Content -LiteralPath (Join-Path $AssetRoot "export_manifest.json") -Raw | ConvertFrom-Json
    if ($manifest.source.glb_sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw "Asset manifest has no valid source GLB SHA-256." }
    if ($manifest.render_model.name -ne "mikotype_keyboard") { throw "Not a production MikoType render model export." }
    $expectedFiles = @("export_manifest.json")
    foreach ($property in $manifest.outputs.PSObject.Properties) {
        $name = $property.Name
        if ($name -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.-]*\.(json|obj|mtl|png)$' -or $name.Contains("..")) { throw "Unsafe exported asset filename." }
        $path = Join-Path $AssetRoot $name
        $item = Get-Item -LiteralPath $path
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Exported assets must be ordinary files." }
        if ($item.Length -ne $property.Value.byte_length -or
            (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $property.Value.sha256.ToLowerInvariant()) {
            throw "Asset validation failed for $name. Re-download the asset ZIP."
        }
        $expectedFiles += $name
    }
    if ("mikotype_keyboard.json" -notin $expectedFiles) { throw "Render model JSON missing from export manifest." }
    foreach ($item in (Get-ChildItem -LiteralPath $AssetRoot -Force)) {
        if ($item.PSIsContainer -or $item.Name -notin $expectedFiles) {
            throw "Asset directory contains undeclared files/directories; extract the WebUI ZIP into a fresh folder."
        }
    }
    return $manifest
}
