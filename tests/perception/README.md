# Production Perception Tests

This directory validates the production hand contract, selected MediaPipe
implementation, sparse-ArUco keyboard pose, measured Contact Map candidates,
same-frame pipeline, and latest-only worker. Tests do not open a camera, access
the network, or initialize the real MediaPipe runtime by default.

Run this module:

    uv run --locked --extra test pytest -q tests/perception

In an activated Conda environment installed with the root README's test extra,
run `pytest -q tests/perception` directly.

The MediaPipe tests inject a local fake runtime to verify BGR-to-RGB conversion,
monotonic VIDEO timestamps, 21-point conversion, frame identity, lifecycle, and
the explicit performance/utilization-metrics acknowledgement guard. A separate
asset-integrity check validates the packaged task bundle without executing it.

Synthetic marker observations and hands verify pose smoothing/coasting,
artifact revision gates, wrong-frame fail-closed behavior, dropped intermediate
frames, processor-error invalidation, input-freeze invalidation, and bounded
shutdown. No Demo adapter participates in these production tests. Real camera
and real MediaPipe inference remain explicit hardware/manual validation.
