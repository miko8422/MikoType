"""Unit tests for the immutable frame contract."""

import pytest

from deskvision.core.models import FramePacket


pytestmark = pytest.mark.unit


class FakeImage:
    shape = (2, 3, 3)


def test_valid_bgr_packet() -> None:
    packet = FramePacket(
        source_id="mac_main",
        frame_id=0,
        acquired_at_ns=123,
        width=3,
        height=2,
        image_bgr=FakeImage(),
    )
    assert packet.image_bgr.shape == (2, 3, 3)


def test_image_shape_must_match_metadata() -> None:
    with pytest.raises(ValueError):
        FramePacket(
            source_id="mac_main",
            frame_id=0,
            acquired_at_ns=123,
            width=3,
            height=2,
            image_bgr=type("WrongImage", (), {"shape": (2, 3, 4)})(),
        )


def test_frame_id_must_be_non_negative() -> None:
    with pytest.raises(ValueError):
        FramePacket(
            source_id="mac_main",
            frame_id=-1,
            acquired_at_ns=123,
            width=3,
            height=2,
            image_bgr=FakeImage(),
        )


def test_timestamp_must_be_non_negative() -> None:
    with pytest.raises(ValueError):
        FramePacket(
            source_id="mac_main",
            frame_id=0,
            acquired_at_ns=-1,
            width=3,
            height=2,
            image_bgr=FakeImage(),
        )
