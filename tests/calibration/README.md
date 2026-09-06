# Calibration Tests

These safe tests validate production Layout, Anchor Reference, multi-frame
registration, five-contact drafts/final maps, artifact revision gates, bundle
path isolation, and the explicit Setup control plane. Synthetic frames and
temporary directories are used; no camera opens and the checked-in production
bundle is never modified.

```bash
.testenv/bin/pytest -q tests/calibration
```
