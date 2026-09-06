from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from deskvision.core.models import FramePacket
from deskvision.state.bundle import FrameStateBundle, LatestFrameStateBundleStore
from deskvision.state.scene_state import SCENE_STATE_SCHEMA_VERSION, SceneState


pytestmark = pytest.mark.unit


def _pair(frame_id: int, *, source_id: str = "camera") -> tuple[FramePacket, SceneState]:
    frame = FramePacket(
        source_id,
        frame_id,
        frame_id + 100,
        2,
        1,
        np.zeros((1, 2, 3), dtype=np.uint8),
    )
    state = SceneState(
        SCENE_STATE_SCHEMA_VERSION,
        source_id,
        frame_id,
        frame.acquired_at_ns,
    )
    return frame, state


def test_bundle_rejects_mismatched_source_identity() -> None:
    frame, _ = _pair(4)
    mismatched = SceneState(SCENE_STATE_SCHEMA_VERSION, "other", 4, 104)

    with pytest.raises(ValueError, match="identity mismatch"):
        FrameStateBundle(frame, mismatched)


def test_bundle_store_keeps_one_latest_pair_and_uses_generation_cursor() -> None:
    store = LatestFrameStateBundleStore()
    first_frame, first_state = _pair(8, source_id="first")
    second_frame, second_state = _pair(0, source_id="restarted")

    first = store.publish(first_frame, first_state)
    second = store.publish(second_frame, second_state)

    assert first.frame is first_frame
    assert second.frame is second_frame
    assert store.latest() is second
    generation, latest = store.latest_with_generation()
    assert generation == 2
    assert latest is second
    assert store.wait_for_newer(1, timeout_s=0) == (2, second)
    assert store.stats().published_count == 2
    assert store.stats().overwritten_count == 1


def test_bundle_wait_timeout_does_not_advance_generation_or_invent_a_pair() -> None:
    store = LatestFrameStateBundleStore()
    frame, state = _pair(2)
    store.publish(frame, state)

    assert store.wait_for_newer(1, timeout_s=0) == (1, None)


def test_bundle_publish_wakes_waiting_consumer() -> None:
    store = LatestFrameStateBundleStore()
    result: list[tuple[int, FrameStateBundle | None]] = []
    waiter = threading.Thread(
        target=lambda: result.append(store.wait_for_newer(0, timeout_s=1.0))
    )
    waiter.start()
    time.sleep(0.01)
    frame, state = _pair(3)
    store.publish(frame, state)
    waiter.join(1.0)

    assert not waiter.is_alive()
    assert result == [(1, store.latest())]
