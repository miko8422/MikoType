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
