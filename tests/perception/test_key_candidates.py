"""Production physical-key candidate mapping tests."""

from __future__ import annotations

import numpy as np
import pytest

from deskvision.calibration.contact_map import (
    build_contact_map,
    build_layout_inventory,
)
from deskvision.core.models import FramePacket
from deskvision.perception.aruco_keyboard import KeyboardPoseEstimate
from deskvision.perception.hand_base import (
    DetectedHand,
    HandLandmark,
    HandTrackingResult,
)
from deskvision.perception.key_candidates import (
    ContactMapEvaluator,
    ContactMapEvaluatorConfig,
)


pytestmark = pytest.mark.unit
IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _frame(frame_id: int = 7) -> FramePacket:
    return FramePacket(
        source_id="camera-a",
        frame_id=frame_id,
        acquired_at_ns=1_000_000_000,
        width=100,
        height=100,
        image_bgr=np.zeros((100, 100, 3), dtype=np.uint8),
    )


def _contact_map():
    inventory = build_layout_inventory(
        {
            "layout_id": "test-layout",
            "calibration_order": ["key_a", "delete_top_right", "function_layer"],
            "keys": [
                {
                    "key_id": "key_a",
                    "label": "A",
                    "browser_code": "KeyA",
                    "model_key_id": "key_a",
                },
                {
                    "key_id": "delete_top_right",
                    "label": "Delete",
                    "browser_code": "Delete",
                    "model_key_id": "pause",
                },
                {
                    "key_id": "function_layer",
                    "label": "Fn",
                    "browser_code": "",
                    "model_key_id": "right_meta",
                },
            ],
        }
    )
    return build_contact_map(
        inventory,
        "anchor-r1",
        {
            "key_a": [(8, 10), (9, 10), (10, 10), (11, 10), (12, 10)],
            "delete_top_right": [
                (18, 10),
                (19, 10),
                (20, 10),
                (21, 10),
                (22, 10),
            ],
            "function_layer": [
                (28, 10),
                (29, 10),
                (30, 10),
                (31, 10),
                (32, 10),
            ],
        },
        created_at="2026-09-05T00:00:00Z",
    )


def _pose(
    frame: FramePacket,
    *,
    reference_revision: str = "anchor-r1",
    status: str = "tracking",
    confidence: float = 1.0,
) -> KeyboardPoseEstimate:
    matrix = IDENTITY if status in {"tracking", "tracking_degraded", "coasting"} else None
    return KeyboardPoseEstimate(
        reference_revision=reference_revision,
        source_id=frame.source_id,
        frame_id=frame.frame_id,
        acquired_at_ns=frame.acquired_at_ns,
        status=status,
        reference_to_image=matrix,
        image_to_reference=matrix,
        marker_ids=(0, 1, 2, 3) if matrix else (),
        unknown_marker_ids=(),
        excluded_marker_ids=(),
        confidence=confidence,
        reprojection_rmse_px=0.0 if matrix else None,
        inlier_ratio=1.0 if matrix else 0.0,
    )


def _hands(
    frame: FramePacket,
    *,
    frame_id: int | None = None,
    handedness: str = "right",
    x: float = 0.1,
    y: float = 0.1,
    score: float = 0.96,
) -> HandTrackingResult:
    landmarks = tuple(
        HandLandmark(x=x, y=y, z=0.0, score=score) for _ in range(21)
    )
    hand = DetectedHand(
        handedness=handedness,
        score=score,
        landmarks=landmarks,
    )
    return HandTrackingResult(
        model_id="mediapipe_hands",
        source_id=frame.source_id,
        frame_id=frame.frame_id if frame_id is None else frame_id,
        acquired_at_ns=frame.acquired_at_ns,
        inference_started_ns=1,
        inference_completed_ns=2,
        hands=(hand,),
    )


def test_maps_five_fingertips_to_top_three_physical_and_model_ids() -> None:
    frame = _frame()
    result = ContactMapEvaluator(_contact_map()).evaluate(
        frame,
        _hands(frame),
        _pose(frame),
    )

    assert result.status == "ready"
    assert result.usable is True
    assert result.anchor_revision == "anchor-r1"
    assert len(result.predictions) == 5
    for prediction in result.predictions:
        assert [candidate.physical_key_id for candidate in prediction.candidates] == [
            "key_a",
            "delete_top_right",
            "function_layer",
        ]
        assert [candidate.model_key_id for candidate in prediction.candidates] == [
            "key_a",
            "pause",
            "right_meta",
        ]
        assert prediction.direct_physical_key_id == "key_a"
        assert prediction.candidates[0].nearest_sample_index == 2
        assert prediction.candidates[0].probability == pytest.approx(0.96)


def test_frame_mismatch_fails_closed_instead_of_pairing_stale_hands() -> None:
    frame = _frame()
    result = ContactMapEvaluator(_contact_map()).evaluate(
        frame,
        _hands(frame, frame_id=frame.frame_id + 1),
        _pose(frame),
    )

    assert result.status == "frame_mismatch"
    assert result.usable is False
    assert result.predictions == ()


def test_anchor_revision_mismatch_fails_closed() -> None:
    frame = _frame()
    result = ContactMapEvaluator(_contact_map()).evaluate(
        frame,
        _hands(frame),
        _pose(frame, reference_revision="anchor-r2"),
    )

    assert result.status == "artifact_mismatch"
    assert result.predictions == ()


@pytest.mark.parametrize(
    ("status", "confidence"),
    (("not_found", 0.0), ("stale_frame", 0.0), ("coasting", 0.1)),
)
def test_unusable_stale_or_low_confidence_pose_clears_candidates(
    status: str,
    confidence: float,
) -> None:
    frame = _frame()
    result = ContactMapEvaluator(_contact_map()).evaluate(
        frame,
        _hands(frame),
        _pose(frame, status=status, confidence=confidence),
    )

    assert result.status == "pose_unusable"
    assert result.predictions == ()


def test_raw_source_swaps_only_handedness_not_coordinates() -> None:
    frame = _frame()
    raw = ContactMapEvaluator(_contact_map()).evaluate(
        frame,
        _hands(frame, handedness="right"),
        _pose(frame),
    )
    mirrored = ContactMapEvaluator(
        _contact_map(),
        ContactMapEvaluatorConfig(source_coordinates_mirrored=True),
    ).evaluate(
        frame,
        _hands(frame, handedness="right"),
        _pose(frame),
    )

    assert all(item.mediapipe_handedness == "right" for item in raw.predictions)
    assert all(item.handedness == "left" for item in raw.predictions)
    assert all(item.handedness == "right" for item in mirrored.predictions)
    assert [
        (item.image_x, item.image_y, item.reference_x, item.reference_y)
        for item in raw.predictions
    ] == [
        (item.image_x, item.image_y, item.reference_x, item.reference_y)
        for item in mirrored.predictions
    ]


def test_direct_requires_final_probability_not_only_nearest_distance() -> None:
    frame = _frame()
    evaluator = ContactMapEvaluator(
        _contact_map(),
        ContactMapEvaluatorConfig(
            min_pose_confidence=0.1,
            direct_probability=0.5,
        ),
    )

    result = evaluator.evaluate(
        frame,
        _hands(frame, score=0.5),
        _pose(frame, confidence=0.5),
    )

    assert result.status == "ready"
    assert result.predictions[0].candidates[0].spatial_weight == pytest.approx(1.0)
    assert result.predictions[0].candidates[0].probability == pytest.approx(0.25)
    assert result.predictions[0].candidates[0].direct is False
    assert result.predictions[0].direct_physical_key_id is None
