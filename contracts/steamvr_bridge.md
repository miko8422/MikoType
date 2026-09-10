# SteamVR bridge v0.1

Production components: `src/deskvision/steamvr` (camera-independent adapter),
`src/deskvision/web/steamvr.py` (same-origin FastAPI), and `integrations/steamvr`
(separately built Windows x64 driver/bridge). Historical Demo stays isolated.

## Local HTTP boundary

Use the service's actual `http://127.0.0.1:<port>` URL. The native client rejects
other hosts, credentials, paths, fragments and redirects and uses WinHTTP
without a proxy; it never changes system network settings.

| Route | Purpose |
| --- | --- |
| `GET /steamvr` | Unified VR control/observability page |
| `GET /api/steamvr/status` | Scene freshness, bridge liveness, model revision, bounded logs |
| `PUT /api/steamvr/settings` | `{enabled,pose_confirmed,x,y,z,yaw,pitch,roll}`; session-only |
| `GET /api/steamvr/bridge-token` | Explicit private credential download; rotate on service restart |
| `GET /api/steamvr/frame.bin` | Latest 96-byte header + RGBA8 image; requires token |
| `POST /api/steamvr/events` | Bounded native structured events; requires token |
| `GET /api/steamvr/preview.png` | Camera-free rendered overlay preview; outlines may remain while native frame is hidden |
| `GET /api/steamvr/driver-assets.zip` | Current model's OBJ/MTL/PNG/JSON + hash manifest |
| `POST /api/steamvr/collect-logs` | Explicit Windows registry-discovered, allowlisted log tails |
| `POST /api/steamvr/feedback` | `{message}` user headset observation |
| `GET /api/steamvr/diagnostics` | Download JSON report excluding token and camera images |

Native authorization header: `X-MikoType-SteamVR-Token`. No token in URLs or
diagnostics. HTTP request bodies are limited to 16 KiB. Events contain
`source: windows-bridge`, `level: info|warning|error`, `event`, `message`,
and optional primitive `details`. Only 300 recent events are retained; routine
heartbeats update liveness without flooding logs. Bridge liveness expires at
6 seconds. Native visual/pose freshness is stricter (1.5 seconds).

## Binary frame

Little-endian, Python format `<8sIIIIQ8f32s`, exactly **96 bytes** before pixels.

| Offset | Type | Field |
| --- | --- | --- |
| 0 | 8 bytes | ASCII `MIKOVR01` |
| 8 | uint32 | Version 1 |
| 12,16 | uint32 each | Width, height in pixels |
| 20 | uint32 | Flags: bit0 scene fresh/pose usable/revisions match; bit1 display enabled; bit2 manual VR pose confirmed |
| 24 | uint64 | Monotonic response sequence, session scoped |
| 32,36 | float32 each | Keyboard width/height in metres |
| 40,44,48 | float32 each | Standing-space x/y/z in metres |
| 52,56,60 | float32 each | Yaw/pitch/roll in degrees; `Ry * Rx * Rz` |
| 64 | 32 bytes | Raw SHA-256 of active adaptive GLB |
| 96 | width × height × 4 | Top-to-bottom RGBA8, no padding |

No video frames or raw camera imagery cross this boundary. Native decoder
validates version, finite values, bounded dimensions/body, flags and installed
model SHA. Each HTTP poll replaces a single latest slot; no growing frame queue.
The camera thread never performs OpenVR calls. Windows `vrserver` never performs
HTTP calls: the bridge sends a fresh, confirmed pose via a session-local bounded
shared-memory channel; the driver reads without blocking its frame loop.

GLB uses X right, Y up, Z toward the keyboard front. Exported Valve OBJ uses
`(x,-z,y)`; Overlay is centered in its XY plane at Z=0.018m. Manual pitch -90°
turns the surface normal upward in standing space. The bridge transforms
standing-space pose back into raw tracking space before handing it to the driver.
This is not HMD-following placement and is not automatic physical 6DoF tracking.

## Gating and diagnostic meaning

- Scene data is gated by capture age, monotonic expiry, marker usability, and
  layout/anchor/contact/model revisions. An expired generation cannot revive
  after wall-clock correction. Stale output contains transparent pixels.
- Installed model mismatch disables pose/Overlay. Re-export and reinstall the
  current model, restart SteamVR, then reconnect the bridge.
- Manual keyboard pose may remain while the camera has no marker; dynamic
  highlights disappear. Service/network/bridge loss invalidates both.
- Rendering surfaces are real 3D keyboard model + 2D fingertip/highlight plane,
  not metric 3D hand reconstruction. Finger position uses an affine fit plus
  continuous measured-contact residual interpolation to the user keyboard plane.
- `tracker_found`/successful Overlay API calls do not certify Home visibility.
  Home process observation and user headset feedback are separate evidence.
- SteamVR file collection reads only `vrserver.txt`, `vrcompositor.txt`,
  `vrmonitor.txt`, `vrclient_steamtours.txt` beneath the current user's registered
  Steam `logs` folder. Each is bounded to 64 KiB / 100 lines. No arbitrary file
  endpoint exists. Missing files are reported, not interpreted as runtime failure.
  Redaction is best-effort; review exports before sharing. A SteamVR System
  Report remains the fallback for deeper Pimax/driver issues.

## Acceptance boundary

Mac verifies API, rendering, model conversion, protocol and portable native math.
Windows CI verifies native compilation. Actual SteamVR/Home/Pimax visibility,
physical alignment, occlusion and end-to-end latency require a Windows headset.

Official references: [OpenVR overlay overview](https://github.com/ValveSoftware/openvr/wiki/IVROverlay_Overview),
[driver documentation and logs](https://github.com/ValveSoftware/openvr/blob/master/docs/Driver_API_Documentation.md),
[OpenVR header / coordinate conventions](https://github.com/ValveSoftware/openvr/blob/master/headers/openvr.h).
