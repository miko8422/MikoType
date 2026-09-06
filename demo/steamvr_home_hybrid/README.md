# SteamVR Home Hybrid Feasibility Demo

This is an isolated feasibility project. It does not modify or get imported by
`src/deskvision`, does not register a SteamVR driver on macOS, and does not
claim that a browser/WebGL result is a SteamVR Home result.

The Demo answers two separate questions:

1. Can the current user-adaptive keyboard asset and `SceneState 0.2` boundary
   be prepared and tested on an Apple Silicon Mac? **Yes.**
2. Can SteamVR Driver loading, SteamVR Home visibility, or headset alignment be
   verified on that Mac? **No. Those gates require Windows x64 + SteamVR + a
   headset.**

Valve ended SteamVR support for macOS in 2020. The current OpenVR SDK can still
be used for header/source-level checks, but its prebuilt macOS library has no
Apple Silicon slice and there is no supported SteamVR Runtime/Home for this
host. Rosetta and the legacy macOS branch are intentionally not treated as the
project's acceptance environment.

The smoke source was checked against OpenVR 2.15.6. Primary references are
Valve's [macOS support announcement](https://store.steampowered.com/news/posts/?appids=250820&enddate=1589495195&feed=steam_community_announcements),
[current Driver documentation](https://github.com/ValveSoftware/openvr/blob/master/docs/Driver_API_Documentation.md),
[Driver Render Model rules](https://github.com/ValveSoftware/openvr/wiki/Driver-Render-Models),
[local Driver registration rules](https://github.com/ValveSoftware/openvr/wiki/Local-Driver-Registration),
and [Overlay overview](https://github.com/ValveSoftware/openvr/wiki/IVROverlay_Overview).

## What the Demo contains

```text
steamvr_home_hybrid/
├── app.py                 loopback FastAPI visual lab (port 8776)
├── environment.py         non-mutating host/runtime capability probe
├── asset_export.py        adaptive GLB validator and Valve asset exporter
├── state_adapter.py       latest-only SceneState revision/TTL gate
├── source_bundle.py       deterministic, fingerprinted Windows handoff ZIP
├── static/                local 3D mock compositor; no CDN or upload UI
├── windows_driver/        Windows GenericTracker Driver source/package root
├── windows_overlay/       separate static Overlay smoke companion source
├── packaging/             Windows build/register/smoke/uninstall scripts
└── output/                ignored, reproducible assets and source ZIP
```

The checked-in production source asset remains:

```text
data/keyboards/kzzi_user_adjustable_82/
├── adaptive_keyboard.glb
└── adaptive_keyboard_manifest.json
```

The converter verifies the manifest digest and revision, all 82
`key:<physical_key_id>` nodes, finite meter-space geometry, and expected bounds.
It then bakes the glTF scene graph and converts the model to Valve Driver Render
Model assets:

- OBJ + MTL + PNG, with one object per OBJ and fewer than 65,000 vertices;
- deterministic UVs for the current solid-color model;
- one static component per material group;
- axis transform `glTF (x, y, z) -> Valve OBJ (x, -z, y)` so the exported
  asset is X-right, Y-forward, Z-up without changing handedness;
- a transparent static highlight texture for Overlay attachment/co-visibility
  smoke testing (not dynamic key-state rendering);
- an export manifest containing source/output SHA-256 hashes and the conversion
  policy.

## Run on the Mac

No camera, Steam process, network service, or SteamVR registration is needed.

```bash
PYTHONPATH=src:. .testenv/bin/python \
  -m demo.steamvr_home_hybrid.app --host 127.0.0.1 --port 8776
```

Open <http://127.0.0.1:8776/>. The page provides:

- a truthful local capability matrix;
- the production adaptive GLB in a local Three.js viewer;
- direct-key and neighbor-glow mock playback;
- one-click deterministic Valve asset validation/export;
- a downloadable Windows smoke **source** bundle.

The Mac gate ends at:

```text
mac_asset_validated -> windows_source_bundle_validated
```

It cannot advance these gates:

```text
windows_driver_built -> steamvr_driver_loaded
  -> home_model_visible -> overlay_aligned
```

## HTTP boundary

The Demo exposes only local, camera-free endpoints:

- `GET /api/status` — platform capabilities and honest gate state;
- `GET /api/model/manifest` — active production model manifest;
- `GET /api/model/keyboard.glb` — active production adaptive GLB;
- `POST /api/export` — validate and generate isolated Demo outputs;
- `GET /api/export/manifest` — download the latest export manifest;
- `GET /api/windows-bundle` — download the Windows smoke source ZIP.

The current production `SceneState 0.2` contains a 2D image Homography, not a
metric keyboard-to-SteamVR six-degree-of-freedom transform. The isolated state
adapter therefore labels its simulator transform `pose_source=demo-fixed`.
Revision mismatch, an unknown model binding, disconnect, or TTL expiry clears
the render state instead of leaving ghost highlights. Duplicate/out-of-order
packets are ignored while the latest accepted render state is retained.

## Windows smoke procedure

Use a Windows x64 machine with SteamVR, a headset, Visual Studio 2022 Desktop
C++, CMake, and a pinned OpenVR SDK. Extract the source bundle, then use an x64
PowerShell prompt:

```powershell
.\packaging\build.ps1 -OpenVrSdkRoot "C:\path\to\openvr"
.\packaging\install.ps1
```

Restart SteamVR after registration and make sure the add-on is enabled. First
check `vrserver.txt` for `TrackedDeviceAdded`, then inspect the empty SteamVR
environment, and only then enter SteamVR Home. The Driver registers one
`TrackedDeviceClass_GenericTracker` with serial
`DESKVISION-KEYBOARD-DEMO-001` and points it at
`{deskvisionkeyboard}deskvision_keyboard`.

The Driver uses a fixed head-relative pose only for this visibility smoke. It
does no HTTP, WebSocket, JSON parsing, or blocking work inside `vrserver`.
The separate Overlay companion can load the generated transparent PNG and bind
it to that device for a static attachment/co-visibility smoke. Dynamic
FastAPI/WebSocket
consumption remains the next Windows implementation step, after Home model
visibility is proven.

Run the supplied smoke script and record these as distinct results:

1. Windows x64 Driver builds and lands under the required `bin/win64` path.
2. SteamVR loads exactly one local Driver registration.
3. The GenericTracker exists and its custom keyboard Render Model parses.
4. SteamVR Home chooses to keep that third-party model visible.
5. The model is approximately 35 x 15 cm, upright, and not mirrored.
6. The Overlay is co-visible and aligned with the key plane.

If step 3 succeeds but step 4 fails, stop there: Home's rendering policy is a
separate result from Driver/OBJ correctness. Do not silently disguise the
keyboard as a controller. Remove the development registration with the exact
path script when finished:

```powershell
.\packaging\uninstall.ps1
```

## Deliberately out of scope

- No supported SteamVR Runtime/Home emulation on macOS.
- No claim that the current 2D ArUco Homography solves VR-world alignment.
- No dynamic WebSocket client inside the Driver process.
- No Controller impersonation or SteamVR input injection.
- No production merge; success here is Demo feasibility only.

Automated checks and their exact boundary live in
[`tests/demo/steamvr_home_hybrid/README.md`](../../tests/demo/steamvr_home_hybrid/README.md).
