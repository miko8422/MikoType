# Production Web Tests

These tests use injected stores, virtual listeners, and temporary artifacts to
validate the unified local control console. They prove that snapshot and
WebSocket bytes come from the exact processed frame paired with SceneState,
stale data fails closed, and a running process keeps immutable
Layout/GLB/Manifest snapshots across setup publication. They also cover
allowlisted local settings, protected fields, atomic override/reset behavior,
service self-description, default automatic selection across 9000–10000,
legacy-port migration, reserved/excluded Windows ports, and explicit strict
selection. Unit tests inject listeners; `test_binding_integration.py` uses
OS-assigned loopback ports to verify real socket ownership and service identity
with an inherited proxy. It never opens a camera or reconfigures another process.
Host/origin isolation tests cover HTTP
mutations, DNS rebinding attempts, and browser WebSocket access to camera
bundles.

```bash
uv run --locked --extra test pytest -q tests/web
```

In an activated Conda environment installed with the root README's test extra,
run `pytest -q tests/web` directly.

## Mac-first non-VR validation

The production console is also exercised with `configs/macos.yaml`; this is
the same setup, localization, contact-map, and adaptive-model implementation
as Windows, not a second Demo service. Automated tests continue to use fake
camera devices and temporary profiles. They must not enumerate real cameras,
change `configs/*.local.yaml`, modify `data/local/macos/`, or start SteamVR.
Camera discovery/selection tests should inject their device inventory and
runtime callbacks and check invalid input, switching failures, and stale state
separately from the hardware-free HTTP/WebSocket contract tests.

For explicit manual acceptance, start the production source launcher with the
Mac profile as described in the root README. In the resulting single WebUI,
verify camera refresh/selection under `/settings`, layout edits and complete
Marker/contact calibration under `/setup`, then same-frame hand tracking,
key-state highlights, and adaptive GLB download under `/`. That session writes
only the Mac profile's ignored
settings and active/staging artifact paths. It is a hardware session, not part
of the default `pytest` run. Record actual outcomes rather than treating a
passing synthetic suite as camera/calibration acceptance.

SteamVR Home, headset visibility, and camera-to-VR alignment remain separate
Windows + HMD checks for `integrations/steamvr`; production adapter regressions
live in `tests/steamvr`, while historical smoke tests remain in `tests/demo/steamvr_home_hybrid`;
they are not prerequisites for this WebUI suite or Mac service startup.

### Observed Mac hardware smoke — 2026-09-09 / 0.1.0.dev3

The local service on port 9000 enumerated **MacBook Pro相机**, device index 0,
through AVFoundation. Camera capture and MediaPipe ran without observed read
errors; the actual feed was 1280×720 at approximately 30 FPS despite a 60 FPS
request. Reopening the same selected camera succeeded. This verifies the
tested single-device path, not switching between two different physical cameras.

No real keyboard markers were present in that scene, so this smoke does **not**
accept physical key accuracy. Remaining manual checks are real-marker
registration/localization, the user's five-touch calibration for every key,
candidate/highlight accuracy with those artifacts, and independent Windows +
SteamVR Home/headset acceptance. The cyan raw hand overlay can be inspected
without markers; green mapped bubbles must not be expected until keyboard
localization is usable. These observations are separate from synthetic test
results and are not a claimed end-to-end calibration pass.
