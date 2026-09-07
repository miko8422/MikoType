# State Tests

These tests cover SceneState 0.2, its one-slot state store, and the atomic
processed `FramePacket + SceneState` bundle store. Identity mismatches fail and
slow consumers receive only the newest generation.

```bash
uv run --locked --extra test pytest -q tests/state
```

In an activated Conda environment installed with the root README's test extra,
run `pytest -q tests/state` directly.
