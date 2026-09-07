# Adaptive Keyboard Tests

These tests generate deterministic in-memory GLB data from temporary calibrated
profiles, verify one `key:<physical_key_id>` node per key, enforce geometry and
revision checks, and cover direct/neighbor Bubble aggregation. They do not run
a browser or GPU renderer.

```bash
uv run --locked --extra test pytest -q tests/keyboard
```

In an activated Conda environment installed with the root README's test extra,
run `pytest -q tests/keyboard` directly.
