# MikoType

[English](README.md) | [简体中文](README.zh-CN.md)

MikoType is a camera-based physical-keyboard mapping and interaction pipeline
for VR experiments. It tracks hands with MediaPipe, establishes a stable
keyboard reference frame from sparse ArUco markers, maps fingertips to a
user-calibrated key layout, generates an adaptive 3D keyboard, and publishes a
latest-only scene state for local visualization and future VR consumers.

> **V0.1 status:** the production vision and keyboard-mapping pipeline is
> implemented and validated on macOS. Windows camera execution and the
> SteamVR/Home consumer are still explicit acceptance targets, not completed
> production support.

The Python distribution and CLI retain the compatibility names
`vr-desk-vision` and `deskvision`; MikoType is the project and repository name.

## What is implemented

- Low-latency, latest-frame-only OpenCV capture with lifecycle and health
  metrics.
- MediaPipe 21-landmark tracking for up to two hands.
- Sparse `DICT_4X4_50` ArUco anchors for a stable keyboard reference frame.
- User layout inventory, five-contact-per-key calibration, and revision-gated
  artifacts.
- Fingertip Bubble evaluation with direct-key and neighbor-glow output.
- A self-contained adaptive GLB with one `key:<physical_key_id>` node per key.
- A local FastAPI inspector and WebSocket `SceneState 0.2` stream.
- An isolated OpenVR Driver/Render Model feasibility package for Windows
  headset testing.

The runtime never claims that a highlighted key is a mechanical keypress or an
operating-system keyboard event.

## Runtime flow

```text
Camera
  -> FramePacket (one in-process BGR frame)
  -> MediaPipe Hands + ArUco keyboard pose
  -> calibrated fingertip-to-key candidates
  -> direct/neighbor interaction state
  -> latest SceneState + local WebUI/WebSocket
  -> future Windows SteamVR consumer
```

All realtime stages use the same source frame identity. Slow consumers do not
build a backlog: superseded frames and scene states are discarded.

## Repository layout

```text
src/deskvision/     production runtime
configs/            macOS and experimental Windows configurations
data/keyboards/     calibrated sample keyboard bundle and generated GLB
contracts/          external scene-state schema
tests/              isolated automated tests
demo/               selected experiment; never imported by production
windows_vr/         production integration boundary
```

## Requirements

- Python 3.11 or 3.12 is recommended.
- A webcam visible to OpenCV.
- The bundled sample keyboard artifacts, or a new calibration produced through
  the setup workflow.
- Explicit acknowledgement of MediaPipe Tasks performance/utilization metrics
  when starting hand tracking.

## macOS quick start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[test]'

.venv/bin/python -m deskvision.main check --config configs/dev.yaml
.venv/bin/python -m deskvision.main run \
  --config configs/dev.yaml \
  --acknowledge-mediapipe-metrics
```

Grant Camera permission to the terminal or host application, then open
<http://127.0.0.1:8765/>.

To edit the layout, register anchors, or rebuild contact calibration:

```bash
.venv/bin/python -m deskvision.main setup \
  --config configs/dev.yaml \
  --acknowledge-mediapipe-metrics
```

Open <http://127.0.0.1:8765/setup>. Restart the runtime after applying a newly
completed keyboard bundle.

## Windows quick start (experimental)

The core pipeline can be started on Windows with OpenCV's automatic camera
backend. This path is provided for the next deployment test, but camera latency,
MediaPipe performance, requested capture properties, and shutdown behavior
still require Windows hardware acceptance.

From PowerShell:

```powershell
git clone https://github.com/miko8422/MikoType.git
cd MikoType

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"

.\.venv\Scripts\python.exe -m deskvision.main check --config configs\windows.yaml
.\.venv\Scripts\python.exe -m deskvision.main run `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

Allow desktop applications to access the camera in Windows Privacy settings,
then open <http://127.0.0.1:8765/>. If the wrong camera is selected, change
`camera.device_index` in [`configs/windows.yaml`](configs/windows.yaml).

The setup workflow uses the same configuration:

```powershell
.\.venv\Scripts\python.exe -m deskvision.main setup `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

## SteamVR/Home feasibility path

SteamVR itself is not supported on modern macOS; Valve ended macOS support in
2020. MikoType therefore keeps the OpenVR experiment isolated from the
production vision process.

To inspect the model and generate the Windows source handoff:

```bash
PYTHONPATH=src:. .venv/bin/python \
  -m demo.steamvr_home_hybrid.app --host 127.0.0.1 --port 8776
```

Open <http://127.0.0.1:8776/> and choose **Validate and generate source
bundle**. On a Windows x64 machine with SteamVR, a headset, Visual Studio 2022
Desktop C++, CMake, and a pinned OpenVR SDK, extract that bundle and run:

```powershell
.\packaging\build.ps1 -OpenVrSdkRoot "C:\path\to\openvr"
.\packaging\install.ps1
# Restart SteamVR and enable the deskvisionkeyboard add-on.
.\packaging\smoke.ps1
```

This smoke path registers a fixed-pose `GenericTracker`, attaches the adaptive
keyboard Render Model, and tests a keyboard-aspect static Overlay. It does **not**
yet provide calibrated camera-to-SteamVR 6DoF alignment or live dynamic
highlights. SteamVR Home visibility must be confirmed in a Windows headset
before that bridge is promoted into production. Remove the development driver
registration with `.\packaging\uninstall.ps1` when finished.

## Validation

The default suite excludes camera hardware and long-running soak tests:

```bash
.venv/bin/python -m pytest -q
```

Explicit hardware checks:

```bash
.venv/bin/python -m pytest -q -m "hardware and not soak"
.venv/bin/python -m pytest -q -m soak
```

## Privacy and safety

- The default web server binds to loopback only.
- Camera frames stay in the local process unless a future transport is
  explicitly configured.
- Setup outputs are revision checked before activation.
- MediaPipe acknowledgement is intentionally required at each unconfigured
  runtime start.
- Experiment and test code is isolated from `src/deskvision` by an architecture
  test.

No open-source license has been selected for this repository yet.
