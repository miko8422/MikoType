# MikoType

[English](README.md) | [简体中文](README.zh-CN.md)

MikoType is a camera-based physical-keyboard mapping and interaction pipeline
for VR. It tracks hands with MediaPipe, establishes a keyboard reference plane
from sparse ArUco markers, maps fingertips to user-calibrated keys, generates
an adaptive 3D keyboard, and publishes revision-gated scene data for a VR
consumer.

> **Current development workflow:** run the shared production vision/mapping
> code and the complete keyboard WebUI locally on macOS first. Windows remains
> the final single-PC VR deployment target; SteamVR Home integration and headset
> acceptance are a separate Windows-only module. The Mac workflow does not
> require SteamVR or a connection to a Windows machine, and does not enable
> distributed inference.

The Python distribution and legacy CLI retain the names `vr-desk-vision` and
`deskvision` for compatibility. For source checkouts, both uv and Conda use
[`run_mikotype.py`](run_mikotype.py). This launcher loads and verifies `src/`
from its own repository, even if an older `mikotype` command or editable install
exists elsewhere. It uses the active Python environment for dependencies.

## What is implemented

- Latest-frame-only OpenCV capture with macOS AVFoundation and Windows MSMF,
  DSHOW, or automatic backend selection, plus WebUI camera selection.
- MediaPipe tracking for up to two hands with 21 landmarks per hand.
- A `DICT_4X4_50` sparse-marker keyboard reference frame.
- User key inventory, five-contact-per-key calibration, and revision checks.
- Fingertip bubbles, likely direct-key contact, and distance-based neighbor
  highlights.
- An adaptive GLB with one `key:<physical_key_id>` node per key.
- A loopback FastAPI inspector, model endpoints, and `SceneState 0.2`
  WebSockets.
- A separate Windows OpenVR GenericTracker driver and live Overlay bridge,
  with a `/steamvr` control/diagnostic page and current-user render-model export.

MikoType reports likely contact candidates. It does not claim a mechanical
keypress or inject operating-system keyboard input.

## V0.1 architecture

```text
One local Mac (vision/mapping validation) or Windows PC

Camera
  -> latest FramePacket
  -> MediaPipe hands + ArUco keyboard pose
  -> calibrated key candidates and highlights
  -> local SceneState + adaptive keyboard GLB
  -> FastAPI on 127.0.0.1
  -> browser: settings, calibration, hands and key-state highlights
     + downloadable adaptive 3D keyboard GLB

Separate Windows acceptance module (not started by the vision service):
  local FastAPI -> live Windows bridge -> GenericTracker 3D keyboard
                                      -> plane Overlay highlights + fingertips
  WebUI /steamvr <- bridge events + explicit SteamVR log collection
  (manual room-space alignment; Windows/headset acceptance required)
```

Every live vision stage uses the same captured frame. Slow downstream work
skips superseded frames instead of building a latency queue. The local service
is restricted to loopback so camera and state data stay on the host in V0.1.

## macOS local development and validation

Use this path to test camera capture, MediaPipe hands, marker localization,
the full keyboard setup workflow, key-state highlights, and adaptive GLB generation before Windows
hardware testing. Use Python 3.12 and the same locked dependencies and
`src/deskvision` code as Windows; no old Demo server is needed.

From this repository in Terminal:

```bash
uv sync --locked --python 3.12 --extra test
uv run --locked python ./run_mikotype.py doctor --config configs/macos.yaml
uv run --locked python ./run_mikotype.py run \
  --config configs/macos.yaml \
  --acknowledge-mediapipe-metrics
```

For an already activated Conda environment installed with `python -m pip
install -e ".[test]"`, omit `uv run --locked`. Do not mix Conda and uv
environments. Allow camera access to the application hosting Python, such as
Terminal or Codex, when macOS prompts. MikoType does not modify system privacy
settings. Close an older camera Demo if it is using the same device.

Open the **actual** `OPEN THIS EXACT URL` printed after startup. The automatic
port range is 9000–10000. Use this single console in order:

1. `/settings`: refresh the camera list, select the intended camera and apply
   it. If the default camera could not open, the console remains available for
   selection/retry. AVFoundation is the explicit Mac backend. Verify the preview
   and actual
   frame metrics; a requested FPS is not a guarantee of hardware throughput.
2. `/setup`: adjust key positions/sizes, save the staging layout, register the
   physical markers, then capture five right-index contacts per key. After a
   complete calibration, apply the keyboard bundle and restart as prompted.
3. `/`: check the cyan raw MediaPipe hand skeleton/fingertips first; these do
   not require a visible keyboard marker. Green bubbles and key highlights
   represent mapped fingertips/candidates and require a usable keyboard pose.
   Download the generated 3D keyboard, then cover markers or move hands out of
   view to check that stale highlights disappear. A highlight is not a keypress.

The Mac profile stores its active keyboard artifacts and staging workspace
under gitignored `data/local/macos/`; the first `run` or `setup` initializes the
local keyboard from the committed sample. Existing local calibration is not
overwritten. `check` is read-only and does not initialize a missing local
profile. User parameters are isolated in `configs/macos.local.yaml`.
Windows' configuration, sample keyboard files, Demo outputs, and test fixtures
are not destinations for Mac WebUI writes. The included keyboard is a seed,
not proof of calibration for a different camera/keyboard setup: register and
calibrate your physical setup before assessing key accuracy.

Camera status reads do not scan or open devices. Scanning is an explicit action;
on Mac it reads AVFoundation's device inventory. If the device is missing from
the list, a manual device-index option is available. Applying a camera switch
briefly pauses tracking, clears previous frame/state data, and saves the choice
only after the new camera opens. It does not remove your keyboard files.
The dedicated camera selector applies live; camera parameters edited in the
ordinary settings form still require a restart.

Changing device/backend keeps existing staging files for review, but blocks
contact sampling and bundle application until markers are registered and
finalized again. Persisted `camera_binding.json` also catches camera/config
changes across restarts. Changing the physical viewpoint cannot be detected
from configuration alone: recheck the marker reference and contact accuracy.
The Mac
browser's key-state view validates the mapping pipeline only; SteamVR Home visibility,
headset input, and camera-to-VR spatial alignment are tested separately on
Windows.

## Windows local quick start

Requirements:

- Windows 10/11 x64 and Git.
- Python 3.12 x64, managed with uv (recommended) or Conda.
- A webcam visible to OpenCV.
- Windows Camera privacy access for desktop applications.
- The included keyboard bundle, or a bundle produced by the setup workflow.

### Option A: uv (recommended)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) once with
WinGet, then open a new PowerShell:

```powershell
winget install --id=astral-sh.uv -e

git clone https://github.com/miko8422/MikoType.git
cd MikoType

uv sync --locked --python 3.12 --extra test

uv run --locked python .\run_mikotype.py check --config configs\windows.yaml
uv run --locked python .\run_mikotype.py run `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

The committed [`uv.lock`](uv.lock) makes this the reproducible setup path and
uv manages the project-local `.venv` automatically.

### Option B: Conda

After installing a Windows [Conda distribution](https://docs.conda.io/projects/conda/en/stable/user-guide/install/windows.html),
open its PowerShell prompt. To use an ordinary PowerShell instead, run
`conda init powershell` once from that prompt and then reopen PowerShell:

```powershell
git clone https://github.com/miko8422/MikoType.git
cd MikoType

conda create --name mikotype --override-channels --channel conda-forge `
  python=3.12 pip --yes
conda activate mikotype
python -m pip install -e ".[test]"

python .\run_mikotype.py check --config configs\windows.yaml
python .\run_mikotype.py run `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

For an existing Conda installation, update from the repository root and verify
the interpreter before reinstalling. `sys.executable` should point into the
`mikotype` Conda environment. If it does not, reopen the Conda PowerShell Prompt
and activate that environment first:

```powershell
git pull --ff-only
conda activate mikotype
python -c "import sys; print(sys.executable)"
python -m pip install -e ".[test]"
python .\run_mikotype.py --version
python .\run_mikotype.py doctor --config configs\windows.yaml
python .\run_mikotype.py run `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

For this revision, `--version` reports `MikoType 0.1.0.dev4` and the source path
inside this checkout. `doctor` reports installation/config-file diagnostics;
`"status": "ready"` is not Windows camera, network, or SteamVR acceptance. The
launcher also sets its working directory to the repository root, so relative
CLI paths are interpreted from there. To inspect an old bare command separately:

```powershell
Get-Command mikotype -All -ErrorAction SilentlyContinue
python -m pip show vr-desk-vision
python -c "import deskvision; print(deskvision.__file__)"
```

`WinError 10048` means another process already owns the requested socket. If
8765 displays **Pimax Client**, the browser has reached that application's
server. The old log line `MikoType running at http://127.0.0.1:8765` was printed
before the old server bound its port; it did not prove startup succeeded. A
copied log with the same timestamps and PID cannot establish that an updated
checkout has been run. Use a fresh source-launcher invocation and compare its
version, source path, and `OPEN THIS EXACT URL` output. This separates an old
installation from a new bind failure without guessing which environment ran.

Do not combine the uv and Conda environments. Commands below show the uv form.
Inside the activated Conda environment, do not invoke uv: replace
`uv run --locked python .\run_mikotype.py <subcommand>` with
`python .\run_mikotype.py <subcommand>`, keep only `python -m ...` from commands
that start that way after the uv prefix, and replace test commands such as
`uv run --locked --extra test pytest -q` with `python -m pytest -q`. Conda
provides the same isolation, but only the recommended uv path consumes the
exact cross-platform dependency lock.

When the runtime is ready, the terminal prints `OPEN THIS EXACT URL: ...`.
Open that actual URL; the default automatic range is 9000–10000. One process
and one camera then host the complete local control console:

- `/` shows the exact-frame video, hand/keyboard state, quality metrics, and
  key highlights in a perspective key-state view, plus an adaptive GLB download.
  The current production page does not load/render the GLB itself.
- `/settings` selects the active camera and validates/saves allowlisted
  camera, preview, MediaPipe, Marker, interaction, pipeline, and diagnostic
  parameters.
- `/setup` adjusts the existing keys' positions and sizes, registers Marker
  anchors, captures contacts in the layout's key order, and rebuilds the
  adaptive 3D keyboard.

WebUI settings are written atomically to the gitignored
`configs/windows.local.yaml`. The camera selection action can apply a device
change to the current session; the ordinary parameter editor marks changes that
still need a restart. Follow the page's result rather than assuming every
saved setting is already active.

By default, `run` and `setup` select within **9000–10000 inclusive**. A preferred
port inside that range is tried first, followed by the remaining ports from
9000 upward. Old configuration or local-override values such as 8765 are skipped
in automatic mode: selection starts at 9000 without rewriting the saved value.
A matching MikoType instance for the same configuration and Setup workspace
is reused; otherwise the first available candidate is exclusively reserved
before the camera opens, and that same socket is passed to the web server.
Occupied or Windows-reserved ports are skipped. If the whole range is
unavailable, startup fails before opening the camera.

MikoType never terminates or reconfigures the process that owns an occupied
port. Pimax software and every other local service are left running and the scan
simply continues. The identity probes connect directly to loopback and bypass an
inherited HTTP(S) proxy only for those requests; they do not change Windows
proxy settings, VPN state, routes, or another application's networking.

Always open the address printed after `OPEN THIS EXACT URL:`. To require the
preferred port instead of allowing the default scan, opt into strict mode:

```powershell
uv run --locked python .\run_mikotype.py run `
  --config configs\windows.yaml `
  --port 9500 `
  --strict-port `
  --acknowledge-mediapipe-metrics
```

Strict mode honors the explicit/configured port, including a port outside
9000–10000, and fails before camera startup if it is unavailable or belongs to
an incompatible process. Start the optional Windows SteamVR bridge separately;
the vision service never launches SteamVR or changes your VPN/Pimax settings.

The default camera backend is `msmf`. If that camera cannot open reliably, use
the Settings page to try `dshow`, then `any`. Refresh/select a camera in the
camera panel if the wrong device is active. V0.1 requests resolution/FPS but does not yet verify
that every camera driver accepted those values.

## Keyboard setup (shared Mac/Windows control plane)

With the main `run` command active, open its Setup page from the control console
at the exact URL printed by the terminal; do not assume a fixed port or start a
second service. The compatibility command below starts the same integrated
control plane when no matching MikoType instance is running, or prints the
exact existing Setup URL when one is already present:

```powershell
uv run --locked python .\run_mikotype.py setup `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

The setup flow uses the existing layout's key inventory and calibration order,
lets the user correct every key's position and size, registers marker anchors,
captures five right-index contacts per key, validates all artifact revisions,
and rebuilds the adaptive GLB. Restart the runtime after applying a completed
bundle. Adding/removing key IDs or changing labels/anchor assignments remains a
manual layout-file operation in V0.1.

## Live SteamVR / Home integration on Windows

Use the production integration in [`integrations/steamvr`](integrations/steamvr/README.md),
not the historical `demo/steamvr_home_hybrid` smoke server. Start the main
Windows service normally, then open **`/steamvr` on its actual selected port**.
No additional web port is required.

1. Finish camera/keyboard calibration and restart after applying your bundle.
2. On `/steamvr`, download the current user keyboard's **driver assets** and
   the **session bridge token**. The token is a local credential: keep it private,
   do not commit/share it, and download a new one after restarting MikoType.
3. Prefer the `mikotype-steamvr-windows-x64` artifact from a successful
   [Windows build](https://github.com/miko8422/MikoType/actions/workflows/steamvr-windows.yml)
   matching this native code version. Following the integration README, close
   SteamVR, use `scripts/update-assets.ps1` to replace its seed geometry with
   your exported map, then register the `mikotypekeyboard` driver. Keep the
   extracted folder at that path. This route needs no local C++ build setup.
   Visual Studio Desktop C++, CMake and the
   [OpenVR SDK](https://github.com/ValveSoftware/openvr/releases/tag/v2.15.6)
   remain the source-build fallback. Start SteamVR/Home with your normal Pimax
   setup, then start the bridge with the exact service URL and token file.
   No proxy/VPN/Pimax setting is modified.
4. Set the keyboard position/rotation in `/steamvr`, then explicitly confirm
   the position and enable display. Values are metres in SteamVR standing space;
   pitch `-90°` lays the exported keyboard on a horizontal desk. Adjust while
   viewing the headset. Pose settings are session-only and start disabled.
5. Observe live bridge/Home status, frame freshness and model version. Submit a
   short headset observation, click **collect SteamVR logs**, then **export
   diagnostics**. The JSON includes bounded bridge logs and collected SteamVR
   log tails, but not camera images or the bridge token. Review it before sharing.

The bridge publishes a real 3D render model through a **GenericTracker** and
composites live fingertip bubbles/key highlights through a **2D surface Overlay**.
The thin key outline is also a fallback when Home does not draw GenericTracker
models. Driver registration/API success is not proof that Home displayed a
3D object. Fingertip depth and automatic metric camera-to-VR alignment are not
implemented: moving the physical keyboard or resetting room setup requires
manual realignment. This is Windows/headset acceptance work, not a Mac-verified
SteamVR success claim.

Loss of fresh camera/marker data clears the Overlay; loss of bridge/service,
disable/unconfirm, or mismatched installed model clears the device pose too.
After layout/model changes, re-export/reinstall the assets and restart SteamVR.
The native code is separate from the Python vision runtime; no Demo imports or
SteamVR SDK are required for normal Mac/Windows camera testing.

## Experimental distributed inference boundary

A future experiment may keep camera and SteamVR on Windows while moving model
inference to macOS or a remote server:

```text
Windows camera -> authenticated WSS JPEG frames -> remote/macOS inference
Windows SteamVR <- matched SceneState results  <- remote/macOS inference
```

This path is **not active in V0.1**. The repository preserves only its
versioned, disabled interface contract:

- [`contracts/remote_inference.schema.json`](contracts/remote_inference.schema.json)
  defines the JSON envelopes.
- The reserved route/subprotocol are `/ws/experimental/inference` and
  `mikotype.remote-inference.v0.1`; neither is registered by V0.1.
- `RemoteFrameHeader` is followed by exactly one bounded binary JPEG.
- `RemoteSceneStateEnvelope` must match the same session, sequence, source,
  and frame ID.
- Only one latest frame may be in flight.
- `configs/windows.yaml` keeps `remote_inference.enabled: false`, and the V0.1
  runtime rejects attempts to enable it.

The future adapter still needs authenticated TLS, explicit camera-sharing
consent, reconnect/TTL behavior, clock handling, bandwidth limits, a remote
inference server, and a Windows return-state client. The existing debug
WebSockets are unauthenticated loopback diagnostics and must not be exposed as
a remote video service.

## API boundary

The local browser and same-host Windows VR consumer use:

- `GET /api/state` or diagnostic `WS /ws/state` for `SceneState 0.2`.
  Consumers must clear on disconnect/stale or revision mismatch. The native
  bridge uses the dedicated revision/TTL-gated binary route below, not these
  debug WebSockets.
- `GET /api/layout` for the active physical-key inventory.
- `GET /api/model/manifest` and `GET /api/model/keyboard.glb` for the adaptive
  3D keyboard.
- `GET /api/health` for local diagnostics.
- `/api/steamvr/status`, `/api/steamvr/diagnostics` and explicit
  `POST /api/steamvr/collect-logs` for VR observability. The bridge uses
  token-protected `/api/steamvr/frame.bin` and `/api/steamvr/events`; see the
  [wire/control contract](contracts/steamvr_bridge.md).
- `GET /api/camera` for camera status; explicit `POST /api/camera/scan` and
  `POST /api/camera/select` power the local camera panel, not the VR data path.
- `WS /ws/bundle`, `/snapshot.jpg`, and `/stream.mjpg` for the local inspector,
  not the SteamVR hot path.

## Repository layout

```text
src/deskvision/     shared production vision/mapping runtime
run_mikotype.py     repository-anchored source launcher
configs/            separate macOS and Windows configurations
data/keyboards/     calibrated sample and adaptive GLB
data/local/macos/   ignored Mac calibration/model/staging artifacts
contracts/          SceneState and experimental remote wire contracts
tests/              isolated automated tests
demo/               experiments; never imported by production
dispose/            recoverable retired content; excluded from runtime
windows_vr/         same-host Windows VR integration boundary
integrations/steamvr/ native production driver/bridge, separate Windows build
```

Previously discussed experimental features remain isolated in `demo/`.
`dispose/` holds retired scaffolding and obsolete copies for review or recovery;
it is not a second implementation or a production import path. See its README
for the retained items and original locations.

## Validation

The default suite does not open a camera or start SteamVR:

```powershell
uv run --locked --extra test pytest -q
```

Explicit Windows camera checks:

```powershell
uv run --locked --extra test pytest -q -m "hardware and not soak"
uv run --locked --extra test pytest -q -m soak
```

macOS can run the full non-VR service with `configs/macos.yaml`; Windows uses
`configs/windows.yaml`. A successful automated run does not establish camera
accuracy, requested FPS, or VR hardware acceptance. Keep real camera/manual
checks explicit and separate from the default hardware-free suite.

## Privacy and current limits

- V0.1 binds FastAPI to loopback and keeps frames on the same host.
- Setup remains loopback-only and has no image-upload path.
- Remote inference is disabled and has no operational network adapter.
- Setup artifacts are revision checked before activation.
- MediaPipe acknowledgement is required at each unconfigured runtime start.
- Full Windows camera, shutdown, SteamVR Home visibility, dynamic highlights,
  and 6DoF alignment still require Windows + HMD acceptance.

No open-source license has been selected for this repository yet.
