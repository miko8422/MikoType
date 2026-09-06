"""Production keyboard-pose tests using synthetic measured anchors and frames."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from deskvision.calibration.anchor_reference import (
    AnchorReference,
    create_anchor_reference,
)
from deskvision.core.models import FramePacket
from deskvision.perception.aruco_keyboard import (
    ArucoKeyboardLocator,
    ArucoKeyboardLocatorConfig,
    OpenCVArucoDetector,
    estimate_keyboard_pose,
    image_point_to_reference,
    reference_point_to_image,
)


pytestmark = pytest.mark.unit

REGISTRATION = {
    0: ((100, 100), (120, 100), (120, 120), (100, 120)),
    1: ((300, 100), (320, 100), (320, 120), (300, 120)),
    2: ((100, 300), (120, 300), (120, 320), (100, 320)),
    3: ((300, 300), (320, 300), (320, 320), (300, 320)),
}
MARKER_KEYS = {
    0: "escape",
    1: "delete_top_right",
    2: "left_control",
    3: "arrow_right",
}


def _reference() -> AnchorReference:
    return create_anchor_reference(REGISTRATION, MARKER_KEYS)


def _frame(
    frame_id: int,
    *,
    acquired_at_ns: int,
    image: np.ndarray | None = None,
    source_id: str = "camera-a",
) -> FramePacket:
    if image is None:
        image = np.zeros((480, 640, 3), dtype=np.uint8)
    return FramePacket(
        source_id=source_id,
        frame_id=frame_id,
        acquired_at_ns=acquired_at_ns,
        width=image.shape[1],
        height=image.shape[0],
        image_bgr=image,
    )


def _observations_for_transform(
    reference: AnchorReference,
    matrix: np.ndarray,
) -> dict[int, tuple[tuple[float, float], ...]]:
    return {
        anchor.marker_id: tuple(
            tuple(float(value) for value in point)
            for point in cv2.perspectiveTransform(
                np.asarray(anchor.corners_reference, dtype=np.float64).reshape(
                    1, 4, 2
                ),
                matrix.astype(np.float64),
            )[0]
        )
        for anchor in reference.anchors
    }


class _FakeDetector:
    def __init__(self, observations_by_frame):
        self.observations_by_frame = observations_by_frame
        self.frames: list[FramePacket] = []

    def detect(self, frame: FramePacket):
        self.frames.append(frame)
        return self.observations_by_frame.get(frame.frame_id, {})


def test_opencv_detector_uses_dict_4x4_50_and_original_pixel_coordinates() -> None:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    marker = cv2.aruco.generateImageMarker(dictionary, 7, 56)
    image = np.full((220, 260), 255, dtype=np.uint8)
    image[80:136, 110:166] = marker
    bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    frame = _frame(1, acquired_at_ns=1_000_000_000, image=bgr)

    detector = OpenCVArucoDetector()
    observations = detector.detect(frame)

    assert detector.dictionary_name == "DICT_4X4_50"
    assert 7 in observations
    center = np.mean(np.asarray(observations[7]), axis=0)
    assert center == pytest.approx((137.5, 107.5), abs=2.0)


def test_pose_preserves_frame_identity_quality_and_round_trips_points() -> None:
    reference = _reference()
    transform = np.asarray(
        ((20.0, 1.2, 90.0), (-0.4, 19.0, 60.0), (0.0002, -0.0001, 1.0)),
        dtype=float,
    )
    frame = _frame(8, acquired_at_ns=1_000_000_000)
    observations = {
        **_observations_for_transform(reference, transform),
        42: ((10, 10), (20, 10), (20, 20), (10, 20)),
    }

    pose = estimate_keyboard_pose(reference, frame, observations)
    image_point = reference_point_to_image(pose, (5.4, 6.2))

    assert pose.source_id == frame.source_id
    assert pose.frame_id == frame.frame_id
    assert pose.acquired_at_ns == frame.acquired_at_ns
    assert pose.status == "tracking"
    assert pose.usable is True
    assert pose.confidence > 0.99
    assert pose.reprojection_rmse_px == pytest.approx(0.0, abs=1e-5)
    assert pose.unknown_marker_ids == (42,)
    assert image_point_to_reference(pose, image_point) == pytest.approx((5.4, 6.2))


def test_locator_excludes_marker_keys_and_uses_the_same_frame_packet() -> None:
    reference = _reference()
    transform = np.asarray(((20, 0, 100), (0, 20, 70), (0, 0, 1)), dtype=float)
    observations = _observations_for_transform(reference, transform)
    detector = _FakeDetector({1: observations})
    now_ns = 1_000_000_000
    frame = _frame(1, acquired_at_ns=now_ns)
    locator = ArucoKeyboardLocator(
        reference,
        detector=detector,
        clock_ns=lambda: now_ns,
    )

    pose = locator.locate(frame, excluded_key_ids=("escape",))

    assert detector.frames == [frame]
    assert pose.status == "tracking_degraded"
    assert pose.marker_ids == (1, 2, 3)
    assert pose.excluded_marker_ids == (0,)


def test_locator_damps_jitter_and_tracks_real_motion() -> None:
    reference = _reference()
    base = np.asarray(((20, 0, 100), (0, 20, 70), (0, 0, 1)), dtype=float)
    jitter = np.asarray(((20, 0, 102), (0, 20, 71), (0, 0, 1)), dtype=float)
    move = np.asarray(((20, 0, 160), (0, 20, 105), (0, 0, 1)), dtype=float)
    detector = _FakeDetector(
        {
            1: _observations_for_transform(reference, base),
            2: _observations_for_transform(reference, jitter),
            3: _observations_for_transform(reference, move),
        }
    )
    now = [1_000_000_000]
    locator = ArucoKeyboardLocator(
        reference,
        detector=detector,
        clock_ns=lambda: now[0],
    )

    first = locator.locate(_frame(1, acquired_at_ns=now[0]))
    now[0] += 33_000_000
    second = locator.locate(_frame(2, acquired_at_ns=now[0]))
    now[0] += 33_000_000
    third = locator.locate(_frame(3, acquired_at_ns=now[0]))
    first_origin = np.asarray(reference_point_to_image(first, (0, 0)))
    second_origin = np.asarray(reference_point_to_image(second, (0, 0)))
    third_origin = np.asarray(reference_point_to_image(third, (0, 0)))

    assert np.linalg.norm(second_origin - first_origin) < 1.0
    assert np.linalg.norm(third_origin - second_origin) > 30.0


def test_unconfirmed_jump_coasts_then_accepts_a_consistent_second_frame() -> None:
    reference = _reference()
    base = np.asarray(((20, 0, 100), (0, 20, 70), (0, 0, 1)), dtype=float)
    jump = np.asarray(((20, 0, 1200), (0, 20, 800), (0, 0, 1)), dtype=float)
    detector = _FakeDetector(
        {
            1: _observations_for_transform(reference, base),
            2: _observations_for_transform(reference, jump),
            3: _observations_for_transform(reference, jump),
        }
    )
    now = [1_000_000_000]
    locator = ArucoKeyboardLocator(
        reference,
        detector=detector,
        config=ArucoKeyboardLocatorConfig(max_coast_ms=200),
        clock_ns=lambda: now[0],
    )
    locator.locate(_frame(1, acquired_at_ns=now[0]))
    now[0] += 33_000_000
    first_jump = locator.locate(_frame(2, acquired_at_ns=now[0]))
    now[0] += 33_000_000
    confirmed = locator.locate(_frame(3, acquired_at_ns=now[0]))

    assert first_jump.status == "coasting"
    assert first_jump.reason == "unconfirmed_pose_jump"
    assert confirmed.status == "tracking"
    assert reference_point_to_image(confirmed, (0, 0))[0] == pytest.approx(1200, abs=1)


def test_marker_loss_coasts_only_within_budget_then_fails_closed() -> None:
    reference = _reference()
    transform = np.asarray(((20, 0, 100), (0, 20, 70), (0, 0, 1)), dtype=float)
    detector = _FakeDetector({1: _observations_for_transform(reference, transform)})
    now = [1_000_000_000]
    locator = ArucoKeyboardLocator(
        reference,
        detector=detector,
        config=ArucoKeyboardLocatorConfig(max_coast_ms=200),
        clock_ns=lambda: now[0],
    )
    locator.locate(_frame(1, acquired_at_ns=now[0]))
    now[0] += 100_000_000
    coast = locator.locate(_frame(2, acquired_at_ns=now[0]))
    now[0] += 201_000_000
    lost = locator.locate(_frame(3, acquired_at_ns=now[0]))

    assert coast.status == "coasting"
    assert coast.coast_age_ms == pytest.approx(100.0)
    assert 0 < coast.confidence < 1
    assert lost.status == "not_found"
    assert lost.usable is False
    assert lost.reference_to_image is None


def test_duplicate_and_over_age_frames_fail_closed_without_detection() -> None:
    reference = _reference()
    transform = np.asarray(((20, 0, 100), (0, 20, 70), (0, 0, 1)), dtype=float)
    detector = _FakeDetector({1: _observations_for_transform(reference, transform)})
    now = [1_000_000_000]
    locator = ArucoKeyboardLocator(
        reference,
        detector=detector,
        config=ArucoKeyboardLocatorConfig(max_frame_age_ms=100),
        clock_ns=lambda: now[0],
    )
    frame = _frame(1, acquired_at_ns=now[0])
    assert locator.locate(frame).usable
    duplicate = locator.locate(frame)
    now[0] += 250_000_000
    old = locator.locate(_frame(2, acquired_at_ns=1_050_000_000))

    assert duplicate.status == "stale_frame"
    assert duplicate.reason == "duplicate_or_out_of_order_frame"
    assert old.status == "stale_frame"
    assert old.reason == "frame_age_exceeded"
    assert len(detector.frames) == 1
