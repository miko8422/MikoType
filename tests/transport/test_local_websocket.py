import pytest

from deskvision.state.scene_state import SCENE_STATE_SCHEMA_VERSION, SceneState
from deskvision.state.store import LatestSceneStateStore
from deskvision.transport.local_websocket import LocalWebSocketPublisher


pytestmark = pytest.mark.unit


def test_local_publisher_keeps_only_the_latest_state() -> None:
    store = LatestSceneStateStore()
    publisher = LocalWebSocketPublisher(store)
    for frame_id in (1, 2):
        publisher.publish(
            SceneState(
                schema_version=SCENE_STATE_SCHEMA_VERSION,
                source_id="camera",
                source_frame_id=frame_id,
                captured_at_ns=frame_id,
                emitted_at_ns=frame_id,
            )
        )

    assert store.latest().source_frame_id == 2
    assert store.stats().overwritten_count == 1
    assert publisher.stats().published_count == 2
