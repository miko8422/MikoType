from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from deskvision.core.models import FramePacket
from deskvision.perception.aruco_keyboard import KeyboardPoseEstimate
from deskvision.perception.hand_base import DetectedHand, HandLandmark, HandTrackingResult
from deskvision.perception.key_candidates import (
    FingertipKeyPrediction,
    KeyCandidateResult,
    PhysicalKeyCandidate,
)
from deskvision.perception.pipeline import ProductionMappingPipeline
from deskvision.state.scene_state import ArtifactRevisions, KeyboardModelState


pytestmark = pytest.mark.unit
IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _frame() -> FramePacket:
    return FramePacket(
        "camera",
        8,
        1_000,
        20,
        10,
        np.zeros((10, 20, 3), dtype=np.uint8),
    )


def _hands(frame: FramePacket) -> HandTrackingResult:
    landmarks = tuple(HandLandmark(0.5, 0.5) for _ in range(21))
    return HandTrackingResult(
        "mediapipe_hands",
        frame.source_id,
        frame.frame_id,
        frame.acquired_at_ns,
        1_000,
        2_000,
        (DetectedHand("left", 0.9, landmarks),),
    )


def _pose(frame: FramePacket, *, usable: bool = True) -> KeyboardPoseEstimate:
    return KeyboardPoseEstimate(
        reference_revision="anchor",
        source_id=frame.source_id,
        frame_id=frame.frame_id,
        acquired_at_ns=frame.acquired_at_ns,
        status="tracking" if usable else "not_found",
        reference_to_image=IDENTITY if usable else None,
        image_to_reference=IDENTITY if usable else None,
        marker_ids=(0, 1, 2, 3) if usable else (),
        unknown_marker_ids=(),
        excluded_marker_ids=(),
        confidence=0.8 if usable else 0.0,
    )


class _HandTracker:
    def track(self, frame: FramePacket) -> HandTrackingResult:
        return _hands(frame)

    def close(self) -> None:
        pass


class _Locator:
    def __init__(self, pose: KeyboardPoseEstimate) -> None:
        self.pose = pose

    def locate(self, frame: FramePacket) -> KeyboardPoseEstimate:
        return self.pose


class _Mapper:
    def map(self, frame, hands, pose) -> KeyCandidateResult:
        first = PhysicalKeyCandidate(
            "key_a", "key_a", "A", 1, 0.1, 0, 0.9, 0.8, True
        )
        neighbor = PhysicalKeyCandidate(
            "key_s", "key_s", "S", 2, 0.4, 1, 0.6, 0.4, False
        )
        prediction = FingertipKeyPrediction(
            "0:index", 0, "right", "left", "index", 0.9,
            0.5, 0.5, 1.0, 2.0, 0.8, (first, neighbor), "key_a"
        )
        return KeyCandidateResult(
            frame.source_id,
            frame.frame_id,
            frame.acquired_at_ns,
            "ready",
            "layout",
            "layout-revision",
            "anchor",
            "contact",
            pose.status,
            pose.confidence,
            (prediction,),
        )


def _pipeline(frame: FramePacket) -> ProductionMappingPipeline:
    return ProductionMappingPipeline(
        hand_tracker=_HandTracker(),
        keyboard_locator=_Locator(_pose(frame)),
        key_mapper=_Mapper(),
        artifact_revisions=ArtifactRevisions(
            "layout-content", "layout-revision", "anchor", "contact", "model-revision"
        ),
        model=KeyboardModelState(
            "model-revision",
            "0" * 64,
            "/api/model/keyboard.glb",
            "/api/model/manifest",
            82,
        ),
        clock_ns=lambda: 2_000,
        monotonic_ns=iter((0, 1_000, 2_000, 3_000, 4_000, 5_000)).__next__,
    )


def test_pipeline_builds_one_same_frame_transport_state() -> None:
    frame = _frame()

    state = _pipeline(frame).process(frame)
    payload = state.to_dict()

    assert payload["source_frame_id"] == frame.frame_id
    assert payload["hands"][0]["handedness"] == "left"
    assert payload["fingertips"][0]["candidates"][0]["physical_key_id"] == "key_a"
    assert payload["key_highlights"][0]["model_node_id"] == "key:key_a"
    assert payload["key_highlights"][0]["intensity"] == pytest.approx(0.8)
    assert payload["key_highlights"][1]["intensity"] == pytest.approx(0.22)
    assert payload["keyboard"]["key_semantics"] == "likely-contact-not-mechanical-keypress"
    assert "image_bgr" not in str(payload)


def test_pipeline_fails_closed_when_pose_is_unusable() -> None:
    frame = _frame()
    pipeline = _pipeline(frame)
    pipeline.keyboard_locator = _Locator(_pose(frame, usable=False))

    state = pipeline.process(frame)

    assert state.keyboard.pose.usable is False
    assert state.fingertips == ()
    assert state.key_highlights == ()
    assert state.diagnostics.status == "degraded"


def test_pipeline_does_not_commit_wrong_frame_hand_result() -> None:
    frame = _frame()
    pipeline = _pipeline(frame)

    class _WrongFrameHandTracker(_HandTracker):
        def track(self, current: FramePacket) -> HandTrackingResult:
            return replace(_hands(current), frame_id=current.frame_id + 1)

    pipeline.hand_tracker = _WrongFrameHandTracker()

    state = pipeline.process(frame)

    assert state.source_frame_id == frame.frame_id
    assert state.hands == ()
    assert state.fingertips == ()
    assert state.key_highlights == ()
    assert state.diagnostics.status == "degraded"
    assert "same-frame identity mismatch" in (state.diagnostics.error or "")


def test_pipeline_does_not_commit_wrong_frame_keyboard_pose() -> None:
    frame = _frame()
    pipeline = _pipeline(frame)
    pipeline.keyboard_locator = _Locator(
        replace(_pose(frame), acquired_at_ns=frame.acquired_at_ns + 1)
    )

    state = pipeline.process(frame)

    assert state.source_frame_id == frame.frame_id
    assert len(state.hands) == 1
    assert state.keyboard.pose.usable is False
    assert state.keyboard.pose.reference_to_image is None
    assert state.fingertips == ()
    assert state.key_highlights == ()
    assert state.diagnostics.status == "degraded"
    assert "same-frame identity mismatch" in (state.diagnostics.error or "")


def test_pipeline_does_not_commit_wrong_frame_key_candidates() -> None:
    frame = _frame()
    pipeline = _pipeline(frame)

    class _WrongFrameMapper(_Mapper):
        def map(self, current, hands, pose) -> KeyCandidateResult:
            return replace(
                super().map(current, hands, pose),
                source_id="another-camera",
            )

    pipeline.key_mapper = _WrongFrameMapper()

    state = pipeline.process(frame)

    assert state.source_frame_id == frame.frame_id
    assert len(state.hands) == 1
    assert state.keyboard.pose.usable is True
    assert state.fingertips == ()
    assert state.key_highlights == ()
    assert state.diagnostics.status == "degraded"
    assert "same-frame identity mismatch" in (state.diagnostics.error or "")
