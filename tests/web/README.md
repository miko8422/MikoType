# Production Web Tests

These tests use injected stores and temporary artifacts to validate the local
Inspector. They prove that snapshot and WebSocket bytes come from the exact
processed frame paired with SceneState, stale data fails closed, and a running
process keeps immutable Layout/GLB/Manifest snapshots across setup publication.

```bash
.testenv/bin/pytest -q tests/web
```
