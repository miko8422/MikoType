"""Single capture thread that publishes into a latest-frame-only store."""

from __future__ import annotations

from collections import deque
from threading import Event, Lock, Thread, current_thread
import time
from typing import Callable

from deskvision.observability.health import HealthSnapshot
from deskvision.video.latest_frame import LatestFrameStore
from deskvision.video.source import FrameSource


class CaptureStartError(RuntimeError):
    """The source could not be opened before the capture thread started."""


class CaptureStopError(RuntimeError):
    """The capture thread did not terminate within the shutdown deadline."""


class CaptureThread:
    """Own the source lifecycle and continuously publish the newest frame.

    Only frame metadata and a bounded timestamp window are kept for metrics;
    the image itself exists only in LatestFrameStore and the latest packet
    reference used to calculate frame age.
    """

    def __init__(
        self,
        source: FrameSource,
        store: LatestFrameStore,
        *,
        stale_frame_threshold_ms: int = 500,
        read_failure_backoff_s: float = 0.01,
        clock_ns: Callable[[], int] = time.time_ns,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if stale_frame_threshold_ms <= 0:
            raise ValueError("stale_frame_threshold_ms must be positive")
        if read_failure_backoff_s < 0:
            raise ValueError("read_failure_backoff_s must be non-negative")
        self.source = source
        self.store = store
        self.stale_frame_threshold_ms = stale_frame_threshold_ms
        self.read_failure_backoff_s = read_failure_backoff_s
        self._clock_ns = clock_ns
        self._monotonic_ns = monotonic_ns
        self._stop_event = Event()
        self._state_lock = Lock()
        self._thread: Thread | None = None
        self._status = "stopped"
        self._last_error: str | None = None
        self._last_frame = None
        self._read_failures = 0
        self._frames_captured = 0
        self._frame_times_ns: deque[int] = deque(maxlen=60)

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
        try:
            if not self.source.is_open:
                self.source.open()
            if not self.source.is_open:
                raise CaptureStartError("camera source reported closed after open")
        except Exception as exc:
            self._set_status("error", str(exc))
            if isinstance(exc, CaptureStartError):
                raise
            raise CaptureStartError(f"could not start camera capture: {exc}") from exc

        self._stop_event.clear()
        with self._state_lock:
            self._status = "starting"
            self._last_error = None
            self._thread = Thread(
                target=self._run,
                name="deskvision-camera-capture",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        if timeout_s < 0:
            raise ValueError("timeout_s must be non-negative")
        self._stop_event.set()
        with self._state_lock:
            thread = self._thread

        if thread is None:
            try:
                self.source.close()
            except Exception as exc:
                self._set_status("error", str(exc))
                raise
        elif thread is not current_thread():
            # Let the capture thread leave read() and release the backend in
            # its own finally block. This avoids racing VideoCapture release
            # against an in-flight VideoCapture.read().
            thread.join(timeout=timeout_s)
            if thread.is_alive():
                # A backend that remains blocked after the grace period must
                # be interrupted. This is the only path that may release
                # concurrently, and its error is surfaced to the caller.
                try:
                    self.source.close()
                except Exception as exc:
                    self._set_status("error", str(exc))
                    raise
                thread.join(timeout=timeout_s)
                if thread.is_alive():
                    message = (
                        "camera capture thread did not stop after the source was "
                        f"closed and two {timeout_s:.3f}s shutdown waits"
                    )
                    self._set_status("error", message)
                    raise CaptureStopError(message)
        with self._state_lock:
            if thread is None or not thread.is_alive():
                self._status = "stopped"
                self._thread = None

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._thread is not None and self._thread.is_alive()

    def clear_metrics(self) -> None:
        """Reset camera-specific counters only after capture has stopped."""
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("stop capture before clearing camera metrics")
            self._last_frame = None
            self._frame_times_ns.clear()
            self._read_failures = 0
            self._frames_captured = 0
            self._last_error = None

    def metrics(self) -> HealthSnapshot:
        with self._state_lock:
            status = self._status
            last_error = self._last_error
            last_frame = self._last_frame
            read_failures = self._read_failures
            frames_captured = self._frames_captured
            frame_times = tuple(self._frame_times_ns)

        camera_open = self.source.is_open
        frame_age_ms = None
        frame_id = None
        resolution = None
        if last_frame is not None:
            frame_id = last_frame.frame_id
            frame_age_ms = max(
                0.0,
                (self._clock_ns() - last_frame.acquired_at_ns) / 1_000_000,
            )
            resolution = (last_frame.width, last_frame.height)
            if status == "streaming" and frame_age_ms > self.stale_frame_threshold_ms:
                status = "stale"

        capture_fps = 0.0
        if len(frame_times) >= 2:
            elapsed_ns = frame_times[-1] - frame_times[0]
            if elapsed_ns > 0:
                capture_fps = (len(frame_times) - 1) * 1_000_000_000 / elapsed_ns

        return HealthSnapshot(
            status=status,
            camera_open=camera_open,
            capture_fps=capture_fps,
            frame_id=frame_id,
            frame_age_ms=frame_age_ms,
            resolution=resolution,
            read_failures=read_failures,
            last_error=last_error,
            frames_captured=frames_captured,
            overwritten_frames=self.store.stats().overwritten_count,
        )

    def _run(self) -> None:
        self._set_status("streaming", None)
        try:
            while not self._stop_event.is_set():
                try:
                    frame = self.source.read()
                except Exception as exc:
                    self._record_read_failure(str(exc))
                    if self._stop_event.wait(self.read_failure_backoff_s):
                        break
                    continue

                if frame is None:
                    source_error = getattr(self.source, "last_error", None)
                    self._record_read_failure(source_error or "camera read returned no frame")
                    if self._stop_event.wait(self.read_failure_backoff_s):
                        break
                    continue

                try:
                    self.store.publish(frame)
                except Exception as exc:
                    self._set_status("error", f"frame publication failed: {exc}")
                    break
                self._record_success(frame)
        finally:
            try:
                self.source.close()
            except Exception as exc:
                self._set_status("error", str(exc))
            with self._state_lock:
                if self._status != "error":
                    self._status = "stopped" if self._stop_event.is_set() else "error"

    def _record_success(self, frame: object) -> None:
        with self._state_lock:
            self._last_frame = frame
            self._frames_captured += 1
            self._frame_times_ns.append(self._monotonic_ns())
            self._last_error = None
            self._status = "streaming"

    def _record_read_failure(self, error: str) -> None:
        self._set_status("degraded", error)
        with self._state_lock:
            self._read_failures += 1

    def _set_status(self, status: str, error: str | None) -> None:
        with self._state_lock:
            self._status = status
            self._last_error = error
