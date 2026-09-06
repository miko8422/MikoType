from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from deskvision.core.models import FramePacket
from deskvision.perception.worker import LatestFramePerceptionWorker
from deskvision.state.scene_state import SCENE_STATE_SCHEMA_VERSION, SceneState
from deskvision.state.store import LatestSceneStateStore
from deskvision.video.latest_frame import LatestFrameStore


pytestmark = pytest.mark.unit


def _frame(frame_id: int) -> FramePacket:
    return FramePacket(
        source_id="camera",
        frame_id=frame_id,
        acquired_at_ns=frame_id + 10,
        width=2,
        height=2,
        image_bgr=np.zeros((2, 2, 3), dtype=np.uint8),
    )


class _Processor:
    def __init__(self, gate: threading.Event | None = None) -> None:
        self.frames: list[int] = []
        self.gate = gate

    def process(self, frame: FramePacket) -> SceneState:
        self.frames.append(frame.frame_id)
        if self.gate is not None and len(self.frames) == 1:
            self.gate.wait(1.0)
        return SceneState(
            schema_version=SCENE_STATE_SCHEMA_VERSION,
            source_id=frame.source_id,
            source_frame_id=frame.frame_id,
            captured_at_ns=frame.acquired_at_ns,
        )


def _wait_until(predicate, timeout_s: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


def test_worker_processes_same_frame_metadata() -> None:
    frames = LatestFrameStore()
    states = LatestSceneStateStore()
    processor = _Processor()
    worker = LatestFramePerceptionWorker(frames, processor, states)
    worker.start()
    frames.publish(_frame(7))
    _wait_until(lambda: states.latest() is not None)
    worker.stop()

    assert states.latest().source_frame_id == 7
    bundle = states.bundles.latest()
    assert bundle is not None
    assert bundle.frame.frame_id == 7
    assert bundle.state is states.latest()
    assert worker.stats().processed_count == 1


def test_worker_skips_intermediate_frames_instead_of_queueing() -> None:
    frames = LatestFrameStore()
    states = LatestSceneStateStore()
    gate = threading.Event()
    processor = _Processor(gate)
    worker = LatestFramePerceptionWorker(frames, processor, states)
    worker.start()
    frames.publish(_frame(1))
    _wait_until(lambda: processor.frames == [1])
    frames.publish(_frame(2))
    frames.publish(_frame(3))
    frames.publish(_frame(4))
    gate.set()
    _wait_until(lambda: states.latest() is not None and states.latest().source_frame_id == 4)
    worker.stop()

    assert processor.frames == [1, 4]
    assert worker.stats().skipped_frame_count == 2


def test_worker_publishes_empty_same_frame_state_when_processor_fails() -> None:
    class _FailingProcessor:
        def process(self, frame: FramePacket) -> SceneState:
            raise RuntimeError("synthetic failure")

    frames = LatestFrameStore()
    states = LatestSceneStateStore()
    worker = LatestFramePerceptionWorker(frames, _FailingProcessor(), states)
    worker.start()
    frames.publish(_frame(11))
    _wait_until(lambda: states.latest() is not None)
    worker.stop()

    state = states.latest()
    assert state is not None
    assert state.source_frame_id == 11
    assert state.fingertips == ()
    assert state.key_highlights == ()
    assert state.diagnostics.status == "error"
    assert "synthetic failure" in (state.diagnostics.error or "")
    bundle = states.bundles.latest()
    assert bundle is not None
    assert bundle.frame.frame_id == 11
    assert bundle.state is state
    assert worker.stats().last_frame_id == 11


def test_worker_invalidates_highlights_once_when_input_stops_advancing() -> None:
    frames = LatestFrameStore()
    states = LatestSceneStateStore()
    worker = LatestFramePerceptionWorker(
        frames,
        _Processor(),
        states,
        frame_wait_timeout_s=0.005,
        stale_after_ms=20,
    )
    worker.start()
    frames.publish(_frame(12))
    _wait_until(
        lambda: states.latest() is not None
        and states.latest().diagnostics.status == "stale"
    )
    generation, state = states.latest_with_generation()
    time.sleep(0.04)
    worker.stop()

    assert state is not None
    assert state.source_frame_id == 12
    assert state.key_highlights == ()
    assert state.diagnostics.status == "stale"
    assert states.latest_with_generation()[0] == generation
    assert worker.stats().last_frame_id == 12


def test_worker_publishes_only_fail_closed_bundle_when_publisher_fails() -> None:
    class _FailingPublisher:
        def publish(self, state: SceneState) -> None:
            raise RuntimeError("transport unavailable")

    frames = LatestFrameStore()
    states = LatestSceneStateStore()
    worker = LatestFramePerceptionWorker(
        frames,
        _Processor(),
        states,
        publishers=(_FailingPublisher(),),
    )
    worker.start()
    frames.publish(_frame(12))
    _wait_until(lambda: states.bundles.latest() is not None)
    worker.stop()

    bundle = states.bundles.latest()
    assert bundle is not None
    assert bundle.frame.frame_id == 12
    assert bundle.state.diagnostics.status == "error"
    assert bundle.state.key_highlights == ()
    assert states.bundles.stats().published_count == 1
    assert "transport unavailable" in (bundle.state.diagnostics.error or "")
