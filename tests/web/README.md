# Production Web Tests

These tests use injected stores and temporary artifacts to validate the local
Inspector. They prove that snapshot and WebSocket bytes come from the exact
processed frame paired with SceneState, stale data fails closed, and a running
process keeps immutable Layout/GLB/Manifest snapshots across setup publication.

```bash
uv run --locked --extra test pytest -q tests/web
```

In an activated Conda environment installed with the root README's test extra,
run `pytest -q tests/web` directly.
