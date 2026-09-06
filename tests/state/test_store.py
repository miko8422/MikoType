import threading

import pytest

from deskvision.state.scene_state import SCENE_STATE_SCHEMA_VERSION, SceneState
from deskvision.state.store import LatestSceneStateStore


pytestmark = pytest.mark.unit


def _state(frame_id: int) -> SceneState:
    return SceneState(
        schema_version=SCENE_STATE_SCHEMA_VERSION,
        source_id="camera",
        source_frame_id=frame_id,
        captured_at_ns=frame_id,
        emitted_at_ns=frame_id + 1,
    )


def test_latest_state_store_keeps_one_state_and_counts_overwrites() -> None:
    store = LatestSceneStateStore()
    store.publish(_state(1))
    store.publish(_state(4))

    assert store.latest() == _state(4)
    assert store.stats().published_count == 2
    assert store.stats().overwritten_count == 1
    assert store.stats().generation == 2
    assert store.stats().latest_frame_id == 4


def test_latest_state_store_waits_for_a_new_generation() -> None:
    store = LatestSceneStateStore()
    result: list[tuple[int, SceneState | None]] = []
    waiter = threading.Thread(
        target=lambda: result.append(store.wait_for_newer(0, timeout_s=0.5))
    )
    waiter.start()
    store.publish(_state(2))
    waiter.join(1.0)

    assert result == [(1, _state(2))]
