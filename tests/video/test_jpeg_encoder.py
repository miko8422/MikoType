import cv2
import numpy as np
import pytest

from deskvision.core.models import FramePacket
from deskvision.video.jpeg_encoder import LatestJpegEncoder


pytestmark = pytest.mark.unit


def _frame(frame_id: int = 1) -> FramePacket:
    image = np.zeros((20, 40, 3), dtype=np.uint8)
    image[:, :20, 1] = 255
    return FramePacket("camera", frame_id, frame_id, 40, 20, image)


def test_encoder_caches_one_jpeg_per_frame() -> None:
    encoder = LatestJpegEncoder(quality=85)

    first = encoder.encode(_frame())
    second = encoder.encode(_frame())

    assert first == second
    assert first.startswith(b"\xff\xd8")
    assert encoder.stats().encoded_count == 1
    assert encoder.stats().cache_hit_count == 1


def test_encoder_resizes_only_the_debug_copy() -> None:
    frame = _frame()
    encoder = LatestJpegEncoder(max_width=10)

    decoded = cv2.imdecode(
        np.frombuffer(encoder.encode(frame), dtype=np.uint8), cv2.IMREAD_COLOR
    )

    assert decoded.shape[:2] == (5, 10)
    assert frame.image_bgr.shape[:2] == (20, 40)
