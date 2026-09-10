# SteamVR production integration tests

This suite is separate from `tests/demo/steamvr_home_hybrid`. Tests import only
production `deskvision.steamvr` and the production native protocol. They never
open a camera, launch SteamVR, install a driver, alter VPN/Pimax settings, or
write into active keyboard calibration.

Run `uv run --locked pytest tests/steamvr -q` (or `python -m pytest` in Conda).

- Asset conversion: arbitrary key count, model hash/node validation, material
  meshes and metric axis/bounds, flat archive references and deterministic files.
- FastAPI: explicit private session token, bounded bodies/logs, scene/model
  gating, stale clearing, monotonic expiry, continuous fingertip projection,
  manual pose validation, current-user model download and diagnostic export.
- Windows logs: explicit bounded allowlist, redaction, missing files and links;
  Mac returns a clear unsupported result without importing Windows APIs.
- Frontend: status/preview/log polling, safe text rendering, pose editing and
  confirmation, token-only download, feedback/export, existing UI navigation.
- Native portable tests: protocol rejection paths and coordinate transforms.
  Windows build workflow covers SDK compilation separately from headset tests.

Hardware acceptance is still manual: install `integrations/steamvr` on Windows,
observe actual Home/Pimax visibility and alignment, stop capture/bridge, change
model and confirm stale display is cleared. Attach `/api/steamvr/diagnostics`
after explicitly collecting logs and recording what was visible in the headset.

## Verification record — 2026-09-10 / 0.1.0.dev4

The complete hardware-free local suite passed **631 tests** (2 hardware/soak
tests deselected). Production native code at `e0f3fe2` compiled and packaged
successfully on Windows x64 with the pinned official OpenVR SDK:
[Windows build and downloadable artifact](https://github.com/miko8422/MikoType/actions/runs/34479613963).
This proves the DLL/bridge build, not SteamVR Home/Pimax headset visibility.
