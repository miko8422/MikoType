# Calibration Tests

These safe tests validate production Layout, Anchor Reference, multi-frame
registration, five-contact drafts/final maps, artifact revision gates, bundle
path isolation, and the explicit Setup control plane. Synthetic frames and
temporary directories are used; no camera opens and the checked-in production
bundle is never modified.

```bash
uv run --locked --extra test pytest -q tests/calibration
```

In an activated Conda environment installed with the root README's test extra,
run `pytest -q tests/calibration` directly.
