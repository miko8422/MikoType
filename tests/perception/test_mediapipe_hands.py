"""Deterministic checks for the production MediaPipe tracker."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from deskvision.core.models import FramePacket
from deskvision.perception.hand_base import HandTrackerUnavailableError
from deskvision.perception.mediapipe_hands import (
    DEFAULT_MODEL_PATH,
    MediaPipeHandTracker,
    MediaPipeHandTrackerConfig,
)


pytestmark = pytest.mark.unit
EXPECTED_MODEL_SHA256 = (
    "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"
)


class _FakeImage:
    def __init__(self, *, image_format, data) -> None:
        self.image_format = image_format
        self.data = data


class _FakeLandmarker:
    def __init__(self) -> None:
        self.timestamps: list[int] = []
        self.images: list[_FakeImage] = []
        self.close_count = 0

    def detect_for_video(self, image: _FakeImage, timestamp_ms: int):
        self.images.append(image)
        self.timestamps.append(timestamp_ms)
        points = [SimpleNamespace(x=0.25, y=0.5, z=-0.01) for _ in range(21)]
        return SimpleNamespace(
            hand_landmarks=[points],
            hand_world_landmarks=[points],
            handedness=[[SimpleNamespace(category_name="Left", score=0.95)]],
        )

    def close(self) -> None:
        self.close_count += 1


def _frame(frame_id: int, acquired_at_ns: int = 1_000_000) -> FramePacket:
    image = np.zeros((2, 3, 3), dtype=np.uint8)
    image[0, 0] = (10, 20, 30)
    return FramePacket(
        source_id="camera",
        frame_id=frame_id,
        acquired_at_ns=acquired_at_ns,
        width=3,
        height=2,
        image_bgr=image,
    )


def test_runtime_requires_explicit_metrics_acknowledgement() -> None:
    with pytest.raises(HandTrackerUnavailableError, match="acknowledgement"):
        MediaPipeHandTracker()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("num_hands", 0),
        ("min_hand_detection_confidence", -0.1),
        ("min_hand_presence_confidence", 1.1),
        ("min_tracking_confidence", True),
    ),
)
def test_config_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        MediaPipeHandTrackerConfig(**{field: value})


def test_track_uses_rgb_and_monotonic_video_timestamps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    asset = tmp_path / "hand.task"
    asset.write_bytes(b"test model placeholder")
    fake_landmarker = _FakeLandmarker()
    fake_mp = SimpleNamespace(
        Image=_FakeImage,
        ImageFormat=SimpleNamespace(SRGB="srgb"),
    )
    monkeypatch.setattr(
        MediaPipeHandTracker,
        "_load_runtime",
        staticmethod(lambda _path, _config: (fake_mp, fake_landmarker)),
    )
    tracker = MediaPipeHandTracker(
        model_path=asset,
        metrics_acknowledged=True,
    )

    first = tracker.track(_frame(7))
    second = tracker.track(_frame(8))

    assert fake_landmarker.timestamps == [1, 2]
    assert fake_landmarker.images[0].data[0, 0].tolist() == [30, 20, 10]
    assert first.frame_id == 7
    assert first.hands[0].handedness == "left"
    assert len(first.hands[0].landmarks) == 21
    assert len(first.hands[0].world_landmarks) == 21
    assert second.frame_id == 8

    tracker.close()
    tracker.close()
    assert fake_landmarker.close_count == 1
    with pytest.raises(RuntimeError, match="closed"):
        tracker.track(_frame(9))


def test_packaged_model_asset_has_expected_digest() -> None:
    assert DEFAULT_MODEL_PATH.is_file()
    digest = hashlib.sha256(DEFAULT_MODEL_PATH.read_bytes()).hexdigest()
    assert digest == EXPECTED_MODEL_SHA256
