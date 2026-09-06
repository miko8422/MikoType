"""Unit tests for the bounded latest-frame store."""

import pytest

from deskvision.core.models import FramePacket
from deskvision.video.latest_frame import LatestFrameStore


pytestmark = pytest.mark.unit


class FakeImage:
    def __init__(self, width: int = 2, height: int = 1) -> None:
        self.shape = (height, width, 3)


def make_frame(frame_id: int) -> FramePacket:
    return FramePacket(
        source_id="test",
        frame_id=frame_id,
        acquired_at_ns=frame_id,
        width=2,
        height=1,
        image_bgr=FakeImage(),
    )


def test_new_frame_overwrites_old_frame_without_queue_growth() -> None:
    store = LatestFrameStore()
    first = make_frame(0)
    second = make_frame(1)

    store.publish(first)
    store.publish(second)

    assert store.latest() is second
    stats = store.stats()
    assert stats.published_count == 2
    assert stats.overwritten_count == 1
    assert stats.latest_frame_id == 1
    assert store.wait_for_newer(0, timeout_s=0) is second
    assert store.wait_for_newer(1, timeout_s=0) is None


def test_invalid_frame_is_rejected() -> None:
    with pytest.raises(TypeError):
        LatestFrameStore().publish(object())  # type: ignore[arg-type]


def test_negative_timeout_is_rejected() -> None:
    with pytest.raises(ValueError):
        LatestFrameStore().wait_for_newer(timeout_s=-1)
