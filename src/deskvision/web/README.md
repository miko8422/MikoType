# Local Production Control Console (Mac and Windows)

The production runtime exposes one local FastAPI control plane on one loopback
port. Its four browser pages have different authority but share the same
camera, perception worker, exact-frame stores, and immutable live artifacts.

The current workflow validates this shared production pipeline on macOS using
`configs/macos.yaml`, then performs Windows hardware and SteamVR Home acceptance
separately. It does not fork the algorithms into a Mac Demo implementation,
start SteamVR as a dependency, or turn on remote inference.

```bash
uv run --locked python ./run_mikotype.py run \
  --config configs/macos.yaml \
  --acknowledge-mediapipe-metrics
```

Mac uses AVFoundation, isolated `configs/macos.local.yaml` overrides, and
gitignored `data/local/macos/` active/staging artifacts. First `run`/`setup`
seeds the local keyboard from the committed sample without overwriting existing
local calibration; read-only `check` does not seed it. Windows uses
`configs/windows.yaml`. Camera access belongs
to the native Python process, not browser `getUserMedia`; the user may need to
allow camera access for the terminal/app that started Python.

From the repository root, `python .\run_mikotype.py run` serves a read-only
inspector at `/` (prefix with `uv run --locked` when using uv). The launcher
verifies that it loaded this checkout's production source. The browser receives one
single-slot `FramePacket + SceneState` bundle over `/ws/bundle`: metadata/state
first, then the JPEG made from that exact processed frame. It commits the image,
fingertip overlay, and key highlights only after source ID, frame ID, timestamp,
byte length, and image decode agree. Disconnect, stale timeout, or perception
failure immediately clears the visual state.

The algorithm does not consume this JPEG/WebSocket path. MediaPipe, ArUco, and
Contact Map evaluation share the raw BGR frame in process; browser encoding is
only a diagnostic side branch. The cyan raw hand skeleton and fingertips use
MediaPipe results even when no markers are visible. Green mapped bubbles and
key highlights are a separate stage requiring a usable keyboard pose; no green
bubbles does not by itself mean hand tracking failed.

`/settings` exposes an allowlisted configuration editor. It never exposes the
binding host, deployment topology, artifact paths, remote inference, tokens, or
the MediaPipe acknowledgement. Valid changes are written atomically to the
gitignored sibling `*.local.yaml` override; the ordinary settings editor marks
parameters that need a restart. A separate camera panel refreshes available
devices and applies the user's selection to the running session. Camera
selection and parameter saving are distinct operations: respect the result's
live/restart status. Changing a camera/viewpoint requires checking localization
and recalibrating contact samples if the projection accuracy has changed.

Camera API:

- `GET /api/camera` reads status without acquiring camera devices.
- `POST /api/camera/scan` explicitly refreshes the inventory. Mac reads device
  names/indices through AVFoundation; Windows uses bounded probes and skips the
  active camera. A missing result is not proof of absence; the panel also
  accepts a manual index.
- `POST /api/camera/select` takes only `device_index` and `backend`. It pauses
  capture/tracking, clears old live frames/state, applies the device, and saves
  the selection only after a successful camera open. Switching does not delete
  the user's keyboard files, but calibration must be revalidated.

Device/backend changes preserve staging files for inspection but block contact
sampling and bundle application until a fresh marker registration is finalized.
The persisted staging `camera_binding.json` detects camera/config changes across
restarts as well. Moving the camera without changing its configuration remains
a manual revalidation responsibility. Other camera fields in the ordinary
parameter form retain restart-based semantics.

If the initial camera cannot open, the service may remain available with a
camera-unavailable status so the operator can use `/settings` to select/retry.
HTTP readiness must not be interpreted as camera readiness. Ordinary settings
and all camera mutations retain the same Host/Origin protections as Setup.

`/setup` and its mutating API are always mounted on the same service. Setup is
restricted to loopback, has no image upload, keeps Anchor polling single-flight,
and cancels delayed contact capture on Escape, window blur, or a hidden page.
New registration invalidates older staging Anchor/Contact files. Marker cards
come from the user's active layout rather than a hard-coded ID range. Every
request is pinned to the actual loopback Host; browser mutations and
WebSockets carrying a foreign Origin are rejected before they can change setup
state or read camera bundles. Cross-site subresources are rejected and camera
responses opt into same-origin resource protection. The setup workspace is
initialized lazily and cannot overlap production artifacts, so an unavailable
or mistaken staging path cannot damage the active keyboard bundle.

## Guided setup and camera orientation (0.1.0.dev5)

`/setup` opens a read-only overview by default. Tutorial order is camera,
layout, Anchor, contacts, apply/restart, then inspect. Existing valid steps can
be reused; layout has an explicit skip button. Navigation never starts a new
registration or contact session and never saves layout geometry. Missing
artifact information is omitted; available progress is refreshed from the
backend, not a browser-local completion flag.

- `GET /api/setup/status` returns available active/staged artifact summaries,
  validated revisions, saved contact counts, registration readiness, warnings,
  and step availability. It never initializes a staging workspace.
- `GET /api/setup/layout` reads staging if present, otherwise active, without
  copying files. Actual save/start operations initialize staging explicitly.
- `POST /api/camera/view` accepts exactly `mirror_preview` and
  `flip_vertical_preview` booleans. It atomically saves the view and updates
  video/overlay transforms across pages; it does not restart capture.
- `POST /api/camera/orientation` accepts exactly `mirror` and `flip_vertical`
  booleans. It reconfigures capture pixels before both recognition modules.
  The existing camera-change transaction blocks stale contact sampling and
  runtime mapping. After new anchors/contacts are applied, restart loads the
  new bundle; an in-memory old mapper remains blocked until then.

Primary controls are display-only. Advanced input controls have an explicit
calibration warning. No flip is a claim about automatic hardware-mirror
detection, and no setting proves that the physical camera remained stationary.
The worker's calibration guard reads a cached value without a setup lock or
disk I/O, so a camera transaction can safely join the processing worker.

Anchor `sample_count` remains the rolling inlier count for compatibility.
`completion_count` is bounded by `required_samples`; stability/geometry and
lock reasons are separate fields. Frontend shows each separately rather than
displaying an unbounded count as a five-step task. Polling remains single-flight
and stops when the relevant step/page is hidden; delayed captures are cancelled
when leaving calibration. Regression tests use fake cameras and isolated files,
not the user's live calibration.

Production artifact responses are immutable snapshots owned by the running
process. Applying a new bundle changes disk state but cannot mix a new GLB with
old live SceneState; the page tells the operator to restart before the new
revision becomes active.

The reserved experimental remote-inference contract is intentionally not
mounted here. This inspector has no network authentication and must never be
exposed as a cross-device camera service.

Automatic `run` and `setup` selection covers **9000–10000 inclusive**. A preferred
port within the range is tried first, followed by all remaining candidates
from 9000 upward. A legacy config or local-override value outside that range,
including 8765, starts selection at 9000 without rewriting the saved value.
A matching MikoType config revision and Setup workspace in the range is reused
instead of opening a second camera runtime. Otherwise, the first available
candidate is exclusively reserved before camera startup; the same listener is
passed to Uvicorn to avoid releasing and rebinding it. Occupied or Windows-reserved
ports are skipped; an unavailable whole range fails before hardware starts.
`--strict-port` honors the explicit/configured port, even outside 9000–10000,
and disables fallback.

No occupied-port owner is terminated or reconfigured. Pimax software and all
other local services remain running while MikoType continues the scan. Service
identity probes connect directly to loopback and bypass inherited HTTP(S) proxy
settings only for those requests; they do not change the system proxy, VPN,
routes, or another application's networking.

Startup and reuse both print `OPEN THIS EXACT URL: ...`; operators should open
only that address instead of assuming the preferred port was selected. The
actual endpoint is also reported by `/api/service`. The separate SteamVR bridge
requires that exact URL and a session token downloaded from `/steamvr`; it
does not assume a fixed port or require strict-port mode. Wrong services cannot
produce the expected authenticated binary protocol. `/api/service` verifies a
known candidate; it does not reveal an otherwise unknown port by itself.

The former `MikoType running at ...:8765` message preceded binding and could be
followed by `WinError 10048`. It is absent from this revision. Repeated logs
with identical timestamps/PIDs do not show that a new checkout was executed;
verify a fresh launch's version and source path with
`python .\run_mikotype.py --version` and `doctor --config configs\windows.yaml`.
Version 0.1.0.dev3 includes the source launcher, port policy and Mac camera controls. `doctor` diagnoses
installation/config-file state only; it does not prove Mac/Windows camera
acceptance or SteamVR functionality.

## Manual non-VR acceptance on Mac

Use the single URL printed at readiness, not the old Demo ports:

1. `/settings`: refresh/select a camera and verify changing device changes the
   preview. Check frame/processing age and actual throughput; requested FPS
   alone is not a performance measurement.
2. `/setup`: adjust key X/Y/W/H, save staging, register visible markers, and
   collect five same-frame right-index contacts per key. Applying a completed
   bundle writes only this profile's active artifact paths; restart to activate
   its matching Layout/Contact/GLB revision.
3. `/`: verify hand landmarks, likely direct key, and neighbor highlights in
   the perspective key-state view, and download the generated adaptive GLB.
   The current inspector does not load/render the GLB itself.
   Move hands away and occlude markers to test stale-state
   behavior. These are observations, not proof of mechanical key presses.

Browser key-state rendering does not verify headset visibility or spatial alignment.
`integrations/steamvr` is the independently built Windows driver/bridge boundary;
`demo/steamvr_home_hybrid` retains the historical static feasibility work.
This FastAPI process imports neither Demo code nor a SteamVR runtime.

## SteamVR observability (0.1.0.dev4)

`/steamvr` provides pose confirmation, same-model Overlay preview, native bridge
status, bounded logs, headset feedback and downloadable diagnostics. The bridge
downloads latest RGBA/pose through a session-token endpoint. Current calibrated
keyboard assets export from the immutable active runtime snapshot, never a Demo
or stale staged layout. See `contracts/steamvr_bridge.md` for the API/wire format.

Collecting SteamVR file logs is explicit, Windows-only, bounded, and restricted
to known names beneath the registry-discovered Steam log folder. No user file
path endpoint, remote upload, VPN modification or automatic driver install exists.
Mac can validate this page/API/rendering, but cannot certify SteamVR/Home visibility.

This console imports only production modules. Discussed experiments remain in
`demo/`; retired scaffolding and obsolete copies are recoverable in `dispose/`
and are excluded from production runtime imports.
