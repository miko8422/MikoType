# Adaptive Keyboard Tests

These tests generate deterministic in-memory GLB data from temporary calibrated
profiles, verify one `key:<physical_key_id>` node per key, enforce geometry and
revision checks, and cover direct/neighbor Bubble aggregation. They do not run
a browser or GPU renderer.

```bash
.testenv/bin/pytest -q tests/keyboard
```
