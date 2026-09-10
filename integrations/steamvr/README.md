# MikoType SteamVR Home hybrid integration (Windows x64)

This is the optional production integration, separate from historical demos.
The camera, MediaPipe, marker/contact map and adaptive keyboard stay in the
ordinary MikoType service. SteamVR is never a dependency of Mac core testing.

Two native components connect that service to SteamVR:

- `driver_mikotypekeyboard.dll`: registers one GenericTracker with the current
  exported 3D keyboard render model. It reads a bounded, same-Windows-session
  pose record; **no camera, HTTP, inference or blocking waits run in vrserver**.
- `mikotype_steamvr_bridge.exe`: an `VRApplication_Overlay` client. A background
  thread fetches authenticated, bounded RGBA frames from the local service.
  The main thread renders keyboard-plane fingertip bubbles/key highlights,
  publishes a manually confirmed standing-space pose, and reports structured
  status/errors to the same WebUI.

The bridge is an actual OpenVR client, not a browser pretending to run SteamVR.
Native compilation is tested separately in the **SteamVR Windows native build**
GitHub Actions workflow. A successful build does **not** establish headset or
SteamVR Home rendering acceptance; those require a Windows headset session.

## Quick start with a prebuilt Windows artifact

1. Start the normal Windows MikoType service and open its actual printed URL.
   The port is selected dynamically; do not assume 8765 or even 9000 is free.
2. Open `/steamvr`. Download **OpenVR assets** and the **bridge token**. Extract
   the assets ZIP into an otherwise empty folder. The token expires when the
   service restarts; do not commit or share it.
3. Download `mikotype-steamvr-windows-x64` from a successful GitHub Actions run
   for your current commit. Extract it to a stable local folder. It contains
   `dist`, `scripts` and this README. Keep that folder after driver registration.
4. Close SteamVR and update the artifact's seed geometry with your exported map:

```powershell
.\scripts\update-assets.ps1 -AssetDirectory "C:\MikoType\my-openvr-assets"
.\scripts\install.ps1 -SteamVrRoot "C:\Program Files (x86)\Steam\steamapps\common\SteamVR"
```

5. Start SteamVR, enable `mikotypekeyboard` in Manage Add-ons if prompted, and
   enter SteamVR Home. Start the bridge with the service URL/token you downloaded:

```powershell
.\scripts\run.ps1 -ServiceUrl "http://127.0.0.1:9000" -TokenFile "C:\MikoType\mikotype-steamvr.token"
```

6. In `/steamvr`, enable output and adjust/confirm the keyboard's position and
   angles. No pose is published as valid before confirmation. Check the model,
   tips and highlights in the headset, and export the diagnostic report if any
   stage fails. Ctrl+C stops only the bridge. It does not stop MikoType, SteamVR,
   Pimax or your VPN.

On later layout/model changes, repeat export → close SteamVR → `update-assets`
→ restart SteamVR and bridge. The SHA-256 revision gate deliberately hides both
the driver pose and overlay when installed geometry differs from the service.

## Build locally instead

Requirements: Windows x64, SteamVR, a configured headset, Visual Studio 2022
**Desktop development with C++**, CMake 3.21+, and the official
[Valve OpenVR SDK v2.15.6](https://github.com/ValveSoftware/openvr/releases/tag/v2.15.6).
CI pins that release to commit `41bc3825fd35b04047610c86fee26fb33b017b29`.
CI also pins the `windows-2022` runner, rather than a moving `windows-latest`
image that may no longer include the documented Visual Studio 2022 toolchain.
Python/uv/conda do not replace these native C++ build prerequisites.

From the repository root:

```powershell
.\integrations\steamvr\scripts\build.ps1 -OpenVrSdkRoot "C:\SDK\openvr" -AssetDirectory "C:\MikoType\my-openvr-assets"
.\integrations\steamvr\scripts\install.ps1 -SteamVrRoot "C:\Program Files (x86)\Steam\steamapps\common\SteamVR"
.\integrations\steamvr\scripts\run.ps1 -ServiceUrl "http://127.0.0.1:9000" -TokenFile "C:\MikoType\mikotype-steamvr.token"
```

`build.ps1` validates every exported file's digest/length before compiling. It
uses only this integration's `.build` and `dist` directories and performs no
recursive deletion. Install checks the exact existing driver registration
before adding it; it refuses a conflicting path. Uninstall removes only that
exact registration and retains all files:

```powershell
.\integrations\steamvr\scripts\uninstall.ps1 -SteamVrRoot "C:\Program Files (x86)\Steam\steamapps\common\SteamVR"
```

## Coordinate and visibility limits

OpenVR standing space uses metres, +X right, +Y up, -Z forward. Pose rotation is
`Ry(yaw) * Rx(pitch) * Rz(roll)`. A flat desk normally uses `pitch = -90°`.
The bridge converts standing coordinates into the driver's raw tracking space;
moving the headset does **not** move this keyboard. The 3D model uses its own
centred origin, with glTF `(X,Y,Z)` exported as Valve OBJ `(X,-Z,Y)`.

The overlay is a **2D texture on the keyboard plane**, 18 mm above the driver
origin, not a fully reconstructed volumetric hand. MediaPipe contact projections
do not supply metric depth. This v0.1 placement is manually aligned and static:
moving the physical keyboard or resetting SteamVR room origin requires realigning
it. Two-dimensional ArUco homography is not claimed as automatic VR 6DoF tracking.

The driver requests a real GenericTracker render model; an active scene may
choose not to draw generic trackers. `tracker_found` or `overlay_shown` means API
acceptance, **not proof that SteamVR Home visibly renders the 3D keyboard**.
The heartbeat reports the current scene process basename and whether it is
`steamtours.exe`; process permissions can make that observation unavailable.
Headset visibility must still be checked by the user.

## Safety, freshness and diagnostics

Only exact `http://127.0.0.1:PORT` URLs are accepted; HTTP redirects and proxy
discovery are disabled for this bridge. This does not modify system VPN/proxy
settings. Each request has short connection/send/read timeouts and a total read
budget; responses are capped at a 96-byte header plus 16 MiB RGBA.

- No recent service frame for 1.5 seconds: hide overlay and invalidate driver.
- Wrong model fingerprint, output disabled or alignment unlocked: hide both.
- Stale camera/marker contact data: hide dynamic overlay; a confirmed stationary
  3D keyboard may remain visible while the service still responds.
- Bridge crash/exit: the driver independently expires its pose within 1.5 seconds.
- At most one bridge per Windows logon session; pose reads use a zero-timeout
  mutex, so a stuck bridge cannot block vrserver.

The bridge sends `/api/steamvr/events` structured startup, OpenVR initialization,
tracker lookup, revision/freshness gate, overlay-error and two-second heartbeat
events. It never sends camera images or the token in logs. Failures before a valid
token/connection remain visible in the terminal because the service cannot accept
them yet. The driver additionally writes short `MikoType:` entries to SteamVR's
own `vrserver` log. The WebUI diagnostic collection/reporting flow is separate
from the native driver and does not open arbitrary local files.

## Protocol and acceptance checklist

`GET /api/steamvr/frame.bin`, authenticated with `X-MikoType-SteamVR-Token`:

| Offset | Field |
| --- | --- |
| 0 | 8-byte ASCII `MIKOVR01` |
| 8 | uint32 version (=1), width, height, flags |
| 24 | uint64 frame sequence |
| 32 | float32 width_m, height_m, x, y, z, yaw, pitch, roll |
| 64 | 32 raw bytes of active GLB SHA-256 |
| 96 | width × height × 4 RGBA bytes, row-major, image Y downward |

All values are little-endian. Flags: bit 0 contact frame fresh, bit 1 output
enabled, bit 2 manual alignment confirmed. Unexpected flags, size, non-finite
pose or unsupported coordinates are rejected before reaching OpenVR.

Windows headset acceptance should capture these separately:

1. `openvr_ready`, `tracker_found`, heartbeat and `home_active` observations.
2. User sees the correct 3D keyboard, with left/right and orientation correct.
3. Manual placement stays still while the head moves.
4. Right/left fingertip bubbles and key highlights align on that keyboard.
5. Obscure camera/markers: dynamic overlay hides; disconnect service: both hide.
6. Change model revision: `model_mismatch` appears until assets are updated.
7. Close bridge: no stale keyboard remains after its independent pose timeout.
