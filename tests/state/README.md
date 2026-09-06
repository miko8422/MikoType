# State Tests

These tests cover SceneState 0.2, its one-slot state store, and the atomic
processed `FramePacket + SceneState` bundle store. Identity mismatches fail and
slow consumers receive only the newest generation.

```bash
.testenv/bin/pytest -q tests/state
```
