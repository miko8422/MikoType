# SteamVR Home Hybrid Demo Tests

These tests validate the isolated asset work and Windows source handoff
without opening a camera, launching Steam/SteamVR, registering a
Driver, contacting a network service, or writing production data.

```bash
.testenv/bin/pytest -q tests/demo/steamvr_home_hybrid
```

Coverage is split by boundary:

- `test_environment.py` injects non-Windows and Windows fixtures into the read-only
  capability probe. A pretend platform never executes a platform tool.
- `test_asset_export.py` validates the real production GLB/Manifest pair,
  deterministic OBJ/MTL/PNG output, UV/normal/index rules, axis policy, bounds,
  and safe output hashes.
- `test_state_adapter.py` covers `SceneState 0.2` exact revisions, latest-only
  sequence handling, session restart, unknown keys, source changes, fixed-pose
  labelling, and TTL fail-closed behavior.
- `test_windows_bundle.py` checks Driver/Overlay source boundaries, lowercase
  package naming, manifest/resource paths, fixed demo-pose disclosure, and
  exact-path registration/unregistration scripts. It does not compile a
  Windows DLL on a non-Windows test host.
- `test_app.py` checks the loopback API, production asset serving, isolated
  export/ZIP endpoints, and the no-upload/no-false-verification WebUI wording.

An optional manual C++ syntax-only check may use a local OpenVR checkout on a
development host, but it is not a SteamVR runtime acceptance test. All actual DLL ABI,
`vrserver`, compositor, Home visibility, scale/orientation, and headset-space
alignment checks remain manual Windows + HMD gates.
