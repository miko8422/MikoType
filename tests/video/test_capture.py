"""Unit tests for the capture thread without camera hardware."""

from __future__ import annotations

import threading
import time

import pytest

from deskvision.core.models import FramePacket
from deskvision.video.capture import CaptureStartError, CaptureStopError, CaptureThread
from deskvision.video.latest_frame import LatestFrameStore


pytestmark = pytest.mark.unit


class FakeImage:
    shape = (1, 2, 3)


def make_frame(frame_id: int) -> FramePacket:
    return FramePacket(
        source_id="fake",
        frame_id=frame_id,
        acquired_at_ns=time.time_ns(),
        width=2,
        height=1,
        image_bgr=FakeImage(),
    )


class LoopSource:
    def __init__(self) -> None:
        self.opened = False
        self.closed_count = 0
        self.close_threads: list[str] = []
        self.next_id = 0

    @property
    def is_open(self) -> bool:
        return self.opened

    def open(self) -> None:
        self.opened = True

    def read(self) -> FramePacket:
        if not self.opened:
            raise RuntimeError("source closed")
        time.sleep(0.001)
        frame = make_frame(self.next_id)
        self.next_id += 1
        return frame

    def close(self) -> None:
        self.opened = False
        self.closed_count += 1
        self.close_threads.append(threading.current_thread().name)


class FailureThenFramesSource(LoopSource):
    last_error = "simulated read failure"

    def __init__(self) -> None:
        super().__init__()
        self.failures_left = 2

    def read(self) -> FramePacket | None:
        if self.failures_left:
            self.failures_left -= 1
            return None
        return super().read()


class ClosedSource:
    is_open = False

    def open(self) -> None:
        raise RuntimeError("permission denied")

    def close(self) -> None:
        pass


class BlockingSource:
    """Model a backend whose read call remains blocked after close()."""

    def __init__(self) -> None:
        self.opened = False
        self.read_entered = threading.Event()
        self.release_read = threading.Event()

    @property
    def is_open(self) -> bool:
        return self.opened

    def open(self) -> None:
        self.opened = True

    def read(self) -> None:
        self.read_entered.set()
        self.release_read.wait()
        return None

    def close(self) -> None:
        self.opened = False


def test_capture_publishes_frames_and_releases_source() -> None:
    source = LoopSource()
    store = LatestFrameStore()
    capture = CaptureThread(source, store, read_failure_backoff_s=0.001)
    capture.start()
    deadline = time.monotonic() + 1.0
    while capture.metrics().frames_captured < 3 and time.monotonic() < deadline:
        time.sleep(0.005)
    capture.stop()

    metrics = capture.metrics()
    assert metrics.frames_captured >= 3
    assert store.latest() is not None
    assert store.stats().overwritten_count > 0
    assert not source.is_open
    assert source.closed_count >= 1
    assert "deskvision-camera-capture" in source.close_threads
    assert metrics.status == "stopped"


def test_read_failures_are_counted_and_capture_recovers() -> None:
    source = FailureThenFramesSource()
    store = LatestFrameStore()
    capture = CaptureThread(source, store, read_failure_backoff_s=0.001)
    capture.start()
    deadline = time.monotonic() + 1.0
    while capture.metrics().frames_captured < 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    capture.stop()

    metrics = capture.metrics()
    assert metrics.read_failures >= 2
    assert metrics.frames_captured >= 1


def test_open_failure_is_reported() -> None:
    capture = CaptureThread(ClosedSource(), LatestFrameStore())
    with pytest.raises(CaptureStartError):
        capture.start()
    assert capture.metrics().status == "error"
    assert not capture.is_running


def test_stop_reports_a_backend_that_remains_blocked_after_close() -> None:
    source = BlockingSource()
    capture = CaptureThread(source, LatestFrameStore())
    capture.start()
    try:
        assert source.read_entered.wait(timeout=0.5)
        with pytest.raises(CaptureStopError, match="did not stop"):
            capture.stop(timeout_s=0.01)
        assert capture.is_running
        assert capture.metrics().status == "error"
    finally:
        source.release_read.set()
        capture.stop(timeout_s=0.5)
    assert not capture.is_running
