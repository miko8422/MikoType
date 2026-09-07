# MikoType

[English](README.md) | [简体中文](README.zh-CN.md)

MikoType is a camera-based physical-keyboard mapping and interaction pipeline
for VR. It tracks hands with MediaPipe, establishes a keyboard reference plane
from sparse ArUco markers, maps fingertips to user-calibrated keys, generates
an adaptive 3D keyboard, and publishes revision-gated scene data for a VR
consumer.

> **V0.1 deployment scope:** camera capture, MediaPipe/ArUco inference,
> keyboard mapping, the FastAPI state/model service, and the SteamVR consumer
> are all intended to run on the same Windows x64 PC. This is the only V0.1
> production topology. The vision/mapping core is implemented; Windows
> hardware acceptance and the live SteamVR consumer are still incomplete.

The Python distribution and legacy CLI retain the names `vr-desk-vision` and
`deskvision` for compatibility. New instructions use the `mikotype` CLI.

## What is implemented

- Latest-frame-only OpenCV capture with Windows MSMF, DSHOW, or automatic
  backend selection.
- MediaPipe tracking for up to two hands with 21 landmarks per hand.
- A `DICT_4X4_50` sparse-marker keyboard reference frame.
- User key inventory, five-contact-per-key calibration, and revision checks.
- Fingertip bubbles, likely direct-key contact, and distance-based neighbor
  highlights.
- An adaptive GLB with one `key:<physical_key_id>` node per key.
- A loopback FastAPI inspector, model endpoints, and `SceneState 0.2`
  WebSockets.
- A source-only Windows OpenVR Driver/Render Model/Overlay smoke package.

MikoType reports likely contact candidates. It does not claim a mechanical
keypress or inject operating-system keyboard input.

## V0.1 architecture

```text
One Windows x64 PC

Camera
  -> latest FramePacket
  -> MediaPipe hands + ArUco keyboard pose
  -> calibrated key candidates and highlights
  -> local SceneState + adaptive keyboard GLB
  -> FastAPI on 127.0.0.1
  -> same-host SteamVR consumer (next module; not complete)
  -> SteamVR Home / HMD
```

Every live vision stage uses the same captured frame. Slow downstream work
skips superseded frames instead of building a latency queue. The local service
is restricted to loopback so camera and state data do not leave the Windows
machine in V0.1.

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

uv run --locked mikotype check --config configs\windows.yaml
uv run --locked mikotype run `
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

mikotype check --config configs\windows.yaml
mikotype run `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

Do not combine the uv and Conda environments. Commands below show the uv form.
Inside the activated Conda environment, run application commands directly
(omit `uv run --locked`) and replace test commands such as
`uv run --locked --extra test pytest -q` with `pytest -q`. Conda provides the
same isolation, but only the recommended uv path consumes the exact
cross-platform dependency lock.

Open <http://127.0.0.1:8765/>. This starts the current camera, inference,
mapping, model, and local state services. It does not yet start a live SteamVR
consumer.

The default camera backend is `msmf`. If that camera cannot open reliably,
change `camera.backend` in [`configs/windows.yaml`](configs/windows.yaml) to
`dshow`, then `any`. Change `camera.device_index` if OpenCV selects the wrong
camera. V0.1 requests resolution/FPS but does not yet verify that every camera
driver accepted those values.

## Keyboard setup on Windows

```powershell
uv run --locked mikotype setup `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

Open <http://127.0.0.1:8765/setup>. The setup flow defines the user's keys,
registers marker anchors, captures five right-index contacts per key, validates
all artifact revisions, and rebuilds the adaptive GLB. Restart the runtime
after applying a completed bundle.

## SteamVR source-only smoke on the same Windows PC

The current SteamVR work is still an isolated feasibility Demo. Start its
asset lab on the same Windows PC:

```powershell
$env:PYTHONPATH = "$PWD\src;$PWD"
uv run --locked python -m demo.steamvr_home_hybrid.app `
  --host 127.0.0.1 --port 8776
```

Open <http://127.0.0.1:8776/>, then choose **验证并生成源码包 (Validate and
generate source bundle)**. With SteamVR, a headset, Visual Studio 2022 Desktop
C++, CMake, and [OpenVR SDK 2.15.6](https://github.com/ValveSoftware/openvr/releases/tag/v2.15.6)
installed, continue in PowerShell:

```powershell
$SteamVrSmokeDir = Join-Path $PWD ("steamvr-smoke-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
Expand-Archive `
  .\demo\steamvr_home_hybrid\output\deskvision_steamvr_home_windows_source.zip `
  -DestinationPath $SteamVrSmokeDir
Set-Location (Join-Path $SteamVrSmokeDir "deskvision_steamvr_home_smoke_source")

.\packaging\build.ps1 -OpenVrSdkRoot "C:\path\to\openvr"
.\packaging\install.ps1
```

Now restart SteamVR, enable `deskvisionkeyboard` under **Manage Add-ons**, wait
for `vrserver` to become ready, and only then run:

```powershell
.\packaging\smoke.ps1
```

Inspect the SteamVR logs and the headset before cleanup. When the smoke session
is finished:

```powershell
.\packaging\uninstall.ps1
```

This smoke path uses a fixed HMD-relative `GenericTracker` pose and a static
Overlay. It does not consume the live FastAPI state, render dynamic key
highlights, or provide camera-to-SteamVR metric 6DoF alignment. Those remain
the next Windows + HMD implementation and acceptance tasks.

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

The same-host Windows consumer can use:

- `GET /api/state` or the current diagnostic `WS /ws/state` for latest
  `SceneState 0.2`. A future VR consumer must enforce its own TTL from
  `emitted_at_ns` and clear state on disconnect, stale data, or revision/schema
  mismatch; this endpoint does not yet send an explicit stale event.
- `GET /api/layout` for the active physical-key inventory.
- `GET /api/model/manifest` and `GET /api/model/keyboard.glb` for the adaptive
  3D keyboard.
- `GET /api/health` for local diagnostics.
- `WS /ws/bundle`, `/snapshot.jpg`, and `/stream.mjpg` for the local inspector,
  not the SteamVR hot path.

## Repository layout

```text
src/deskvision/     Windows-targeted production vision/mapping runtime
configs/            canonical Windows V0.1 configuration
data/keyboards/     calibrated sample and adaptive GLB
contracts/          SceneState and experimental remote wire contracts
tests/              isolated automated tests
demo/               experiments; never imported by production
windows_vr/         same-host Windows VR integration boundary
```

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

Non-Windows hosts may run offline checks and automated tests, but the
production `run` and `setup` commands fail before opening hardware.

## Privacy and current limits

- V0.1 binds FastAPI to loopback and keeps frames on the same Windows host.
- Setup remains loopback-only and has no image-upload path.
- Remote inference is disabled and has no operational network adapter.
- Setup artifacts are revision checked before activation.
- MediaPipe acknowledgement is required at each unconfigured runtime start.
- Full Windows camera, shutdown, SteamVR Home visibility, dynamic highlights,
  and 6DoF alignment still require Windows + HMD acceptance.

No open-source license has been selected for this repository yet.
