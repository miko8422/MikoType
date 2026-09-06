# SteamVR Home Windows-Local Feasibility Demo

This isolated Demo prepares and checks the source-only OpenVR smoke path for
MikoType's Windows-local topology. It is never imported by `src/deskvision` and
does not claim that a browser/WebGL preview is a SteamVR Home result.

V0.1's intended deployment is one Windows x64 PC running camera capture,
inference, mapping, loopback FastAPI, and the future SteamVR consumer. This Demo
only covers asset conversion, a fixed-pose Driver source, a static Overlay, and
safe Windows packaging. It does not yet connect the live FastAPI state to
SteamVR.

## Contents

```text
steamvr_home_hybrid/
├── app.py                 loopback asset lab on port 8776
├── environment.py         non-mutating host/runtime capability probe
├── asset_export.py        adaptive GLB validator and Valve asset exporter
├── state_adapter.py       offline latest/revision/TTL SceneState gate
├── source_bundle.py       deterministic Windows source ZIP
├── static/                local mock compositor; no upload UI or CDN
├── windows_driver/        fixed-pose GenericTracker Driver source
├── windows_overlay/       static Overlay companion source
├── packaging/             Windows build/install/smoke/uninstall scripts
└── output/                ignored reproducible assets and source ZIP
```

The exporter verifies the adaptive GLB and manifest, all
`key:<physical_key_id>` nodes, finite meter-space geometry, expected bounds,
and content hashes. It produces deterministic Valve OBJ/MTL/PNG assets plus a
manifest and source bundle.

## Run on Windows

From the repository root in PowerShell:

```powershell
$env:PYTHONPATH = "$PWD\src;$PWD"
.\.venv\Scripts\python.exe -m demo.steamvr_home_hybrid.app `
  --host 127.0.0.1 --port 8776
```

Open <http://127.0.0.1:8776/>. The page provides an offline 3D preview,
direct/neighbor highlight playback, deterministic asset export, and a Windows
source ZIP. Click **验证并生成源码包 (Validate and generate source bundle)**
before continuing; ignored `output/` files do not exist in a fresh clone. The
page never launches SteamVR or registers a Driver.

With SteamVR, a headset, Visual Studio 2022 Desktop C++, CMake, and
[OpenVR SDK 2.15.6](https://github.com/ValveSoftware/openvr/releases/tag/v2.15.6)
installed:

```powershell
$SteamVrSmokeDir = Join-Path $PWD ("steamvr-smoke-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
Expand-Archive `
  .\demo\steamvr_home_hybrid\output\deskvision_steamvr_home_windows_source.zip `
  -DestinationPath $SteamVrSmokeDir
Set-Location (Join-Path $SteamVrSmokeDir "deskvision_steamvr_home_smoke_source")

.\packaging\build.ps1 -OpenVrSdkRoot "C:\path\to\openvr"
.\packaging\install.ps1
```

Restart SteamVR, enable `deskvisionkeyboard` under **Manage Add-ons**, wait for
`vrserver` to become ready, and then run:

```powershell
.\packaging\smoke.ps1
```

Inspect the logs and headset output before cleanup. When finished:

```powershell
.\packaging\uninstall.ps1
```

The smoke Driver registers one fixed HMD-relative `GenericTracker` and attaches
the generated keyboard Render Model. The companion displays a static
keyboard-aspect Overlay. Successful compilation or source checks do not prove
SteamVR Home visibility.

## Explicitly out of scope

- No live consumption of `/ws/state` or dynamic key highlights.
- No metric camera-to-SteamVR 6DoF transform.
- No controller impersonation or SteamVR input injection.
- No remote video transmission.
- No claim that offline preview equals headset output.

The experimental Windows-camera-to-remote/macOS-inference interface is a
separate disabled contract under `contracts/`. It is not part of this Demo or
the V0.1 runtime.

## Acceptance gates

1. Validate and export the adaptive keyboard assets.
2. Generate and verify the deterministic Windows source bundle.
3. Build the x64 Driver and Overlay on Windows.
4. Confirm SteamVR loads exactly one local Driver registration.
5. Confirm the model remains visible in SteamVR Home at the expected scale and
   orientation.
6. Implement live loopback SceneState consumption, dynamic highlights, and
   calibrated headset-space alignment before production promotion.
