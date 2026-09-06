# Isolated Video Layer Test UI

This application is test-only. It lives outside src/deskvision, uses a
synthetic source by default, and never changes the production entry point.

## Setup

From the project root:

    python3 -m venv .testenv
    .testenv/bin/python -m pip install -e '.[test]'

## Run isolated tests

    PYTHONPATH=src:. .testenv/bin/pytest -q tests/ui

## Open the dashboard

    PYTHONPATH=src:. .testenv/bin/python -m tests.ui.app --host 127.0.0.1 --port 8766

Then open http://127.0.0.1:8766. The dashboard shows a synthetic moving frame,
the real CaptureThread/LatestFrameStore metrics, browser display FPS, and
estimated capture-to-display latency. The preview uses one WebSocket connection
and keeps only the newest frame; it falls back to serialized no-cache JPEG
requests when WebSocket is unavailable. Use `Open minimal viewer` to open a
separate video-only window. Multiple viewers reuse one encoded JPEG per frame
inside the isolated process, so adding an observer does not multiply the JPEG
encoding cost.

To exercise the real Mac camera through the isolated process, run:

    PYTHONPATH=src:. .testenv/bin/python -m tests.ui.app --source camera --host 127.0.0.1 --port 8766

The Python/Terminal process must have macOS Camera permission for camera mode.
It is safe to run alongside the production package because it uses a separate
process, port, configuration, source selection, and dependency environment.
