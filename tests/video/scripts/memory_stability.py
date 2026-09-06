"""Run the real camera capture loop and check for sustained RSS growth."""

from __future__ import annotations

import argparse
import json
import resource
import statistics
import sys
import time

from deskvision.core.config import CameraConfig
from deskvision.video.capture import CaptureStartError, CaptureThread
from deskvision.video.latest_frame import LatestFrameStore
from deskvision.video.mac_camera import CameraSourceError, MacCameraSource


def _peak_rss_kb() -> float:
    """Return this process's peak resident memory in KiB."""
    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # macOS reports bytes; Linux and most BSD environments report KiB.
    return peak / 1024.0 if sys.platform == "darwin" else peak


def _slope_kb_per_minute(samples: list[tuple[float, float]]) -> float:
    """Calculate the least-squares RSS trend for elapsed-minute samples."""
    x_mean = statistics.fmean(item[0] for item in samples)
    y_mean = statistics.fmean(item[1] for item in samples)
    denominator = sum((x - x_mean) ** 2 for x, _ in samples)
    if denominator == 0:
        return 0.0
    return sum((x - x_mean) * (y - y_mean) for x, y in samples) / denominator


def run_memory_stability(
    *,
    duration_s: float = 900.0,
    warmup_s: float = 60.0,
    sample_interval_s: float = 5.0,
    report_interval_s: float = 60.0,
    max_growth_mb: float = 16.0,
    max_slope_mb_per_min: float = 1.0,
    device_index: int = 0,
) -> int:
    """Return zero when a real-camera soak has no sustained RSS growth."""
    if duration_s <= 0 or warmup_s < 0:
        raise ValueError("duration and warmup must be non-negative, with positive duration")
    if sample_interval_s <= 0 or report_interval_s <= 0:
        raise ValueError("sample and report intervals must be positive")
    if duration_s < warmup_s + sample_interval_s * 4:
        raise ValueError("duration must leave room for at least four post-warmup samples")
    if max_growth_mb < 0 or max_slope_mb_per_min < 0:
        raise ValueError("memory thresholds must be non-negative")

    source = MacCameraSource(CameraConfig(device_index=device_index))
    store = LatestFrameStore()
    capture = CaptureThread(source, store)
    try:
        capture.start()
    except (CameraSourceError, CaptureStartError) as exc:
        print(json.dumps({"status": "failed_to_start", "error": str(exc)}), flush=True)
        return 2

    started = time.monotonic()
    deadline = started + duration_s
    next_sample = started
    next_report = started + report_interval_s
    samples: list[tuple[float, float]] = []
    shutdown_error: str | None = None

    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            time.sleep(min(0.25, max(0.0, deadline - now)))
            now = time.monotonic()
            elapsed_s = now - started
            if now >= next_sample:
                if elapsed_s >= warmup_s:
                    samples.append((elapsed_s / 60.0, _peak_rss_kb()))
                next_sample = now + sample_interval_s
            if now >= next_report:
                metrics = capture.metrics()
                print(
                    json.dumps(
                        {
                            "status": "running",
                            "elapsed_s": round(elapsed_s, 1),
                            "peak_rss_mb": round(_peak_rss_kb() / 1024.0, 3),
                            "frames_captured": metrics.frames_captured,
                            "capture_fps": round(metrics.capture_fps, 3),
                            "read_failures": metrics.read_failures,
                        }
                    ),
                    flush=True,
                )
                next_report = now + report_interval_s
    finally:
        try:
            capture.stop()
        except Exception as exc:  # shutdown failure is part of this acceptance test
            shutdown_error = str(exc)

    metrics = capture.metrics()
    if shutdown_error is not None:
        print(
            json.dumps({"status": "failed_shutdown", "error": shutdown_error}),
            flush=True,
        )
        return 3
    if len(samples) < 4 or metrics.frames_captured == 0:
        print(
            json.dumps(
                {
                    "status": "insufficient_data",
                    "samples": len(samples),
                    "frames_captured": metrics.frames_captured,
                }
            ),
            flush=True,
        )
        return 4

    window = min(12, max(3, len(samples) // 5))
    initial_median_kb = statistics.median(rss for _, rss in samples[:window])
    final_median_kb = statistics.median(rss for _, rss in samples[-window:])
    growth_mb = (final_median_kb - initial_median_kb) / 1024.0
    slope_mb_per_min = _slope_kb_per_minute(samples) / 1024.0
    sustained_growth = growth_mb > max_growth_mb or (
        growth_mb > max_growth_mb / 2.0
        and slope_mb_per_min > max_slope_mb_per_min
    )

    report = {
        "status": "failed" if sustained_growth else "passed",
        "duration_s": round(duration_s, 1),
        "warmup_s": round(warmup_s, 1),
        "samples": len(samples),
        "initial_peak_rss_mb": round(initial_median_kb / 1024.0, 3),
        "final_peak_rss_mb": round(final_median_kb / 1024.0, 3),
        "growth_mb": round(growth_mb, 3),
        "slope_mb_per_min": round(slope_mb_per_min, 4),
        "max_growth_mb": max_growth_mb,
        "max_slope_mb_per_min": max_slope_mb_per_min,
        "frames_captured": metrics.frames_captured,
        "read_failures": metrics.read_failures,
        "camera_open_after_stop": metrics.camera_open,
    }
    print(json.dumps(report), flush=True)
    return 1 if sustained_growth else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=float, default=900.0)
    parser.add_argument("--warmup-seconds", type=float, default=60.0)
    parser.add_argument("--sample-interval-seconds", type=float, default=5.0)
    parser.add_argument("--report-interval-seconds", type=float, default=60.0)
    parser.add_argument("--max-growth-mb", type=float, default=16.0)
    parser.add_argument("--max-slope-mb-per-min", type=float, default=1.0)
    parser.add_argument("--device-index", type=int, default=0)
    args = parser.parse_args()
    return run_memory_stability(
        duration_s=args.duration_seconds,
        warmup_s=args.warmup_seconds,
        sample_interval_s=args.sample_interval_seconds,
        report_interval_s=args.report_interval_seconds,
        max_growth_mb=args.max_growth_mb,
        max_slope_mb_per_min=args.max_slope_mb_per_min,
        device_index=args.device_index,
    )


if __name__ == "__main__":
    raise SystemExit(main())
