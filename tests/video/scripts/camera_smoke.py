"""Manual camera smoke test kept outside the production package."""

from __future__ import annotations

import argparse
import time

from deskvision.core.config import CameraConfig
from deskvision.video.capture import CaptureStartError, CaptureThread
from deskvision.video.latest_frame import LatestFrameStore
from deskvision.video.windows_camera import CameraSourceError, WindowsCameraSource


def run_smoke_test(seconds: float = 5.0, device_index: int = 0) -> int:
    """Open a camera, capture for a bounded interval, and print metrics."""
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    source = WindowsCameraSource(CameraConfig(device_index=device_index))
    store = LatestFrameStore()
    capture = CaptureThread(source, store)
    try:
        capture.start()
    except (CameraSourceError, CaptureStartError) as exc:
        print(f"camera smoke test could not start: {exc}")
        return 2
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(0.1)
        metrics = capture.metrics()
        print(metrics.to_dict())
        if metrics.frames_captured == 0:
            return 1
        return 0
    finally:
        capture.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--device-index", type=int, default=0)
    args = parser.parse_args()
    return run_smoke_test(args.seconds, args.device_index)


if __name__ == "__main__":
    raise SystemExit(main())
