"""Production tests for physical-key Bubble and highlight semantics."""

from dataclasses import replace

import pytest

from deskvision.keyboard.interaction import (
    FingertipBubble,
    KeyHighlight,
    KeyboardInteractionConfig,
    KeyboardPlane,
    KeyboardRect,
    aggregate_key_highlights,
    evaluate_bubble_highlights,
    fingertip_bubbles,
    highlights_from_key_candidates,
)
from deskvision.perception.hand_base import (
    DetectedHand,
    HandLandmark,
    HandTrackingResult,
)
from deskvision.perception.key_candidates import (
    FingertipKeyPrediction,
    KeyCandidateResult,
    PhysicalKeyCandidate,
)


pytestmark = pytest.mark.unit


def _tracking(*, landmark_x: float = 0.2, landmark_score: float = 0.95) -> HandTrackingResult:
    landmarks = tuple(
        HandLandmark(x=landmark_x, y=0.5, score=landmark_score)
        for _ in range(21)
    )
    return HandTrackingResult(
        model_id="fixture-hand-tracker",
        source_id="fixture-camera",
        frame_id=7,
        acquired_at_ns=10,
        inference_started_ns=11,
        inference_completed_ns=12,
        hands=(DetectedHand("right", 0.9, landmarks),),
    )


def test_fingertip_bubbles_use_production_hand_contract_and_mirror_once() -> None:
    bubbles = fingertip_bubbles(_tracking(), KeyboardInteractionConfig())

    assert len(bubbles) == 5
    assert {bubble.finger for bubble in bubbles} == {
        "thumb",
        "index",
        "middle",
        "ring",
        "pinky",
    }
    assert all(bubble.x == pytest.approx(0.8) for bubble in bubbles)
    assert all(bubble.confidence == pytest.approx(0.9) for bubble in bubbles)

    unmirrored = fingertip_bubbles(
        _tracking(), KeyboardInteractionConfig(), mirror_x=False
    )
    assert all(bubble.x == pytest.approx(0.2) for bubble in unmirrored)


def test_low_confidence_fingertips_are_filtered() -> None:
    config = KeyboardInteractionConfig(min_confidence=0.35)
    assert fingertip_bubbles(_tracking(landmark_score=0.2), config) == ()


def test_direct_hit_is_full_strength_and_neighbor_uses_squared_falloff() -> None:
    config = KeyboardInteractionConfig(
        plane=KeyboardPlane(x=0, y=0, width=1, height=1),
        bubble_radius_px=10,
        halo_radius_px=100,
        neighbor_strength=0.4,
    )
    keys = (
        KeyboardRect("key_a", x=0.10, y=0.40, width=0.10, height=0.20),
        KeyboardRect("key_s", x=0.20, y=0.40, width=0.10, height=0.20),
        KeyboardRect("page_up", x=0.80, y=0.10, width=0.08, height=0.12),
    )
    bubble = FingertipBubble(
        bubble_id="0:index",
        hand_index=0,
        handedness="right",
        finger="index",
        x=0.15,
        y=0.50,
        confidence=1.0,
        radius_px=10,
    )

    by_id = {
        value.key_id: value
        for value in evaluate_bubble_highlights(
            keys,
            (bubble,),
            frame_width=1000,
            frame_height=500,
            config=config,
        )
    }

    assert by_id["key_a"].intensity == 1.0
    assert by_id["key_a"].direct is True
    # key_s starts 50 px from the bubble center.  After its 10 px radius,
    # the 100 px halo has 60% influence remaining: 0.4 * 0.6^2.
    assert by_id["key_s"].intensity == pytest.approx(0.144)
    assert by_id["key_s"].direct is False
    assert by_id["page_up"].intensity == 0.0


def test_repeated_physical_key_aggregation_is_ordered_max_or_union() -> None:
    merged = aggregate_key_highlights(
        (
            KeyHighlight("key_a", 0.8, direct=False, contributors=("1:index",)),
            KeyHighlight("key_s", 0.4, direct=False, contributors=("0:index",)),
            KeyHighlight("key_a", 0.3, direct=True, contributors=("0:index",)),
            KeyHighlight("key_a", 0.6, direct=False, contributors=("1:index",)),
        )
    )

    assert [item.key_id for item in merged] == ["key_a", "key_s"]
    assert merged[0] == KeyHighlight(
        "key_a",
        0.8,
        direct=True,
        contributors=("0:index", "1:index"),
    )


def test_duplicate_rectangles_bind_to_one_physical_key_state() -> None:
    config = KeyboardInteractionConfig(
        plane=KeyboardPlane(x=0, y=0, width=1, height=1),
        bubble_radius_px=10,
        halo_radius_px=80,
        neighbor_strength=0.4,
    )
    rectangles = (
        KeyboardRect("space", x=0.10, y=0.40, width=0.10, height=0.20),
        KeyboardRect("space", x=0.60, y=0.40, width=0.20, height=0.20),
    )
    bubble = FingertipBubble(
        bubble_id="0:index",
        hand_index=0,
        handedness="right",
        finger="index",
        x=0.70,
        y=0.50,
        confidence=1.0,
        radius_px=10,
    )

    result = evaluate_bubble_highlights(
        rectangles,
        (bubble,),
        frame_width=1000,
        frame_height=500,
        config=config,
    )

    assert result == (
        KeyHighlight("space", 1.0, direct=True, contributors=("0:index",)),
    )


def _candidate(
    key_id: str,
    probability: float,
    *,
    direct: bool,
) -> PhysicalKeyCandidate:
    return PhysicalKeyCandidate(
        physical_key_id=key_id,
        model_key_id=f"legacy:{key_id}",
        label=key_id,
        rank=1,
        distance_reference=0.1,
        nearest_sample_index=0,
        spatial_weight=0.9,
        probability=probability,
        direct=direct,
    )


def _prediction(
    bubble_id: str,
    candidates: tuple[PhysicalKeyCandidate, ...],
) -> FingertipKeyPrediction:
    return FingertipKeyPrediction(
        bubble_id=bubble_id,
        hand_index=0,
        handedness="right",
        mediapipe_handedness="left",
        finger="index",
        confidence=0.9,
        image_x=0.4,
        image_y=0.5,
        reference_x=2.0,
        reference_y=3.0,
        pose_confidence=0.8,
        candidates=candidates,
        direct_physical_key_id=next(
            (candidate.physical_key_id for candidate in candidates if candidate.direct),
            None,
        ),
    )


def test_ready_candidates_bind_by_physical_id_and_nonready_clears_state() -> None:
    prediction_a = _prediction(
        "0:index",
        (
            _candidate("key_a", 0.75, direct=False),
            _candidate("key_s", 0.20, direct=False),
        ),
    )
    prediction_b = _prediction(
        "1:index",
        (_candidate("key_a", 0.40, direct=True),),
    )
    result = KeyCandidateResult(
        source_id="fixture-camera",
        frame_id=9,
        acquired_at_ns=10,
        status="ready",
        layout_id="fixture-layout",
        layout_revision="layout-revision",
        anchor_revision="anchor-revision",
        contact_map_revision="contact-revision",
        pose_status="tracking",
        pose_confidence=0.8,
        predictions=(prediction_a, prediction_b),
    )

    highlights = highlights_from_key_candidates(result)

    assert highlights == (
        KeyHighlight(
            "key_a",
            0.75,
            direct=True,
            contributors=("0:index", "1:index"),
        ),
        KeyHighlight("key_s", 0.20, contributors=("0:index",)),
    )
    assert all(not value.key_id.startswith("legacy:") for value in highlights)
    assert highlights_from_key_candidates(
        replace(result, status="artifact_mismatch", reason="stale calibration")
    ) == ()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("neighbor_strength", float("nan")),
        ("halo_radius_px", float("inf")),
        ("min_confidence", -0.1),
    ),
)
def test_interaction_config_rejects_invalid_falloff_values(
    field: str,
    value: float,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        KeyboardInteractionConfig(**{field: value})
