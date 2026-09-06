"""Production hand-result contract checks."""

import pytest

from deskvision.perception.hand_base import (
    HAND_LANDMARK_COUNT,
    DetectedHand,
    HandLandmark,
    HandTrackingResult,
)
from deskvision.state.scene_state import SCENE_STATE_SCHEMA_VERSION, SceneState


pytestmark = pytest.mark.unit


def _landmarks() -> tuple[HandLandmark, ...]:
    return tuple(
        HandLandmark(x=index / 20.0, y=0.5, z=-0.01, score=0.9)
        for index in range(HAND_LANDMARK_COUNT)
    )


def test_hand_result_is_frame_correlated_and_scene_state_ready() -> None:
    hand = DetectedHand(
        handedness="right",
        score=0.9,
        landmarks=_landmarks(),
    )
    result = HandTrackingResult(
        model_id="mediapipe_hands",
        source_id="mac_main",
        frame_id=42,
        acquired_at_ns=123,
        inference_started_ns=1_000,
        inference_completed_ns=2_500,
        hands=(hand,),
    )
    payload = result.to_dict()
    state = SceneState(
        schema_version=SCENE_STATE_SCHEMA_VERSION,
        source_id=result.source_id,
        source_frame_id=result.frame_id,
        captured_at_ns=result.acquired_at_ns,
        hands=result.scene_hands(),
    ).to_dict()

    assert payload["schema_version"] == "hand-tracking-0.1"
    assert payload["frame_id"] == 42
    assert payload["latency_ms"] == pytest.approx(0.0015)
    assert len(state["hands"][0]["landmarks"]) == HAND_LANDMARK_COUNT
    assert "image_bgr" not in state


def test_detected_hand_rejects_an_incomplete_landmark_set() -> None:
    with pytest.raises(ValueError, match="21"):
        DetectedHand(
            handedness="left",
            score=0.8,
            landmarks=_landmarks()[:-1],
        )
