"""Map frame-correlated fingertips to a measured physical keyboard map."""

from __future__ import annotations

from dataclasses import dataclass
import math

import cv2
import numpy as np

from deskvision.calibration.contact_map import KeyboardContactMap
from deskvision.core.models import FramePacket
from deskvision.perception.aruco_keyboard import KeyboardPoseEstimate
from deskvision.perception.hand_base import HandTrackingResult


FINGERTIP_LANDMARKS = (
    (4, "thumb"),
    (8, "index"),
    (12, "middle"),
    (16, "ring"),
    (20, "pinky"),
)


class KeyCandidateError(ValueError):
    """The evaluator configuration or contact map is invalid."""


@dataclass(frozen=True, slots=True)
class PhysicalKeyCandidate:
    physical_key_id: str
    model_key_id: str
    label: str
    rank: int
    distance_reference: float
    nearest_sample_index: int
    spatial_weight: float
    probability: float
    direct: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "physical_key_id": self.physical_key_id,
            "model_key_id": self.model_key_id,
            "label": self.label,
            "rank": self.rank,
            "distance_reference": self.distance_reference,
            "nearest_sample_index": self.nearest_sample_index,
            "spatial_weight": self.spatial_weight,
            "probability": self.probability,
            "direct": self.direct,
        }


@dataclass(frozen=True, slots=True)
class FingertipKeyPrediction:
    bubble_id: str
    hand_index: int
    handedness: str
    mediapipe_handedness: str
    finger: str
    confidence: float
    image_x: float
    image_y: float
    reference_x: float
    reference_y: float
    pose_confidence: float
    candidates: tuple[PhysicalKeyCandidate, ...]
    direct_physical_key_id: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "bubble_id": self.bubble_id,
            "hand_index": self.hand_index,
            "handedness": self.handedness,
            "mediapipe_handedness": self.mediapipe_handedness,
            "finger": self.finger,
            "confidence": self.confidence,
            "image_x": self.image_x,
            "image_y": self.image_y,
            "reference_x": self.reference_x,
            "reference_y": self.reference_y,
            "pose_confidence": self.pose_confidence,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "direct_physical_key_id": self.direct_physical_key_id,
        }


@dataclass(frozen=True, slots=True)
class KeyCandidateResult:
    source_id: str
    frame_id: int
    acquired_at_ns: int
    status: str
    layout_id: str
    layout_revision: str
    anchor_revision: str
    contact_map_revision: str
    pose_status: str
    pose_confidence: float
    predictions: tuple[FingertipKeyPrediction, ...]
    reason: str | None = None

    @property
    def usable(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "physical-key-candidates-0.1",
            "source_id": self.source_id,
            "frame_id": self.frame_id,
            "acquired_at_ns": self.acquired_at_ns,
            "status": self.status,
            "usable": self.usable,
            "layout_id": self.layout_id,
            "layout_revision": self.layout_revision,
            "anchor_revision": self.anchor_revision,
            "contact_map_revision": self.contact_map_revision,
            "pose_status": self.pose_status,
            "pose_confidence": self.pose_confidence,
            "predictions": [prediction.to_dict() for prediction in self.predictions],
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ContactMapEvaluatorConfig:
    top_n: int = 3
    distance_scale: float = 1.0
    max_distance: float | None = None
    min_landmark_confidence: float = 0.35
    min_pose_confidence: float = 0.25
    direct_spatial_weight: float = 0.35
    direct_probability: float = 0.18
    source_coordinates_mirrored: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.top_n, bool) or not isinstance(self.top_n, int) or self.top_n < 1:
            raise KeyCandidateError("top_n must be a positive integer")
        if not math.isfinite(self.distance_scale) or self.distance_scale <= 0:
            raise KeyCandidateError("distance_scale must be positive and finite")
        if self.max_distance is not None and (
            not math.isfinite(self.max_distance) or self.max_distance < 0
        ):
            raise KeyCandidateError("max_distance must be non-negative and finite")
        for name, value in (
            ("min_landmark_confidence", self.min_landmark_confidence),
            ("min_pose_confidence", self.min_pose_confidence),
            ("direct_spatial_weight", self.direct_spatial_weight),
            ("direct_probability", self.direct_probability),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise KeyCandidateError(f"{name} must be between 0 and 1")
        if not isinstance(self.source_coordinates_mirrored, bool):
            raise KeyCandidateError("source_coordinates_mirrored must be boolean")


def _physical_handedness(
    mediapipe_handedness: str,
    *,
    source_coordinates_mirrored: bool,
) -> tuple[str, str]:
    raw = mediapipe_handedness.casefold()
    if raw not in {"left", "right"}:
        raw = "unknown"
    if source_coordinates_mirrored:
        physical = raw
    else:
        physical = {"left": "right", "right": "left"}.get(raw, raw)
    return raw, physical


def _image_to_reference(
    pose: KeyboardPoseEstimate,
    x_px: float,
    y_px: float,
) -> tuple[float, float]:
    assert pose.image_to_reference is not None
    projected = cv2.perspectiveTransform(
        np.asarray([[[x_px, y_px]]], dtype=np.float64),
        np.asarray(pose.image_to_reference, dtype=np.float64),
    )[0, 0]
    return float(projected[0]), float(projected[1])


class ContactMapEvaluator:
    """Produce physical key candidates without mutating calibrated artifacts."""

    def __init__(
        self,
        contact_map: KeyboardContactMap,
        config: ContactMapEvaluatorConfig | None = None,
    ) -> None:
        self.contact_map = contact_map
        self.config = config or ContactMapEvaluatorConfig()

    def _empty(
        self,
        frame: FramePacket,
        pose: KeyboardPoseEstimate,
        *,
        status: str,
        reason: str,
    ) -> KeyCandidateResult:
        return KeyCandidateResult(
            source_id=frame.source_id,
            frame_id=frame.frame_id,
            acquired_at_ns=frame.acquired_at_ns,
            status=status,
            layout_id=self.contact_map.layout_id,
            layout_revision=self.contact_map.layout_revision,
            anchor_revision=self.contact_map.anchor_revision,
            contact_map_revision=self.contact_map.revision,
            pose_status=pose.status,
            pose_confidence=pose.confidence,
            predictions=(),
            reason=reason,
        )

    def evaluate(
        self,
        frame: FramePacket,
        hands: HandTrackingResult,
        pose: KeyboardPoseEstimate,
    ) -> KeyCandidateResult:
        identities = {
            (frame.source_id, frame.frame_id, frame.acquired_at_ns),
            (hands.source_id, hands.frame_id, hands.acquired_at_ns),
            (pose.source_id, pose.frame_id, pose.acquired_at_ns),
        }
        if len(identities) != 1:
            return self._empty(
                frame,
                pose,
                status="frame_mismatch",
                reason="frame, hand, and keyboard results must share one identity",
            )
        if pose.reference_revision != self.contact_map.anchor_revision:
            return self._empty(
                frame,
                pose,
                status="artifact_mismatch",
                reason="contact map does not match the active anchor reference",
            )
        if not pose.usable or pose.confidence < self.config.min_pose_confidence:
            return self._empty(
                frame,
                pose,
                status="pose_unusable",
                reason=pose.reason or "keyboard pose is unavailable or below confidence",
            )

        predictions: list[FingertipKeyPrediction] = []
        for hand_index, hand in enumerate(hands.hands):
            mediapipe_hand, physical_hand = _physical_handedness(
                hand.handedness,
                source_coordinates_mirrored=self.config.source_coordinates_mirrored,
            )
            for landmark_index, finger in FINGERTIP_LANDMARKS:
                landmark = hand.landmarks[landmark_index]
                point_confidence = min(hand.score, landmark.score)
                if point_confidence < self.config.min_landmark_confidence:
                    continue
                reference_x, reference_y = _image_to_reference(
                    pose,
                    landmark.x * frame.width,
                    landmark.y * frame.height,
                )
                ranked: list[tuple[float, object, int]] = []
                for key in self.contact_map.keys:
                    if not key.samples:
                        continue
                    distances = [
                        math.dist((reference_x, reference_y), sample)
                        for sample in key.samples
                    ]
                    nearest_index = min(
                        range(len(distances)), key=distances.__getitem__
                    )
                    distance = float(distances[nearest_index])
                    if (
                        self.config.max_distance is not None
                        and distance > self.config.max_distance
                    ):
                        continue
                    ranked.append((distance, key, nearest_index))
                ranked.sort(key=lambda item: (item[0], item[1].key_id))

                candidates: list[PhysicalKeyCandidate] = []
                for rank, (distance, key, nearest_index) in enumerate(
                    ranked[: self.config.top_n], start=1
                ):
                    spatial_weight = math.exp(
                        -0.5 * (distance / self.config.distance_scale) ** 2
                    )
                    probability = float(
                        spatial_weight * point_confidence * pose.confidence
                    )
                    direct = (
                        rank == 1
                        and spatial_weight >= self.config.direct_spatial_weight
                        and probability >= self.config.direct_probability
                    )
                    candidates.append(
                        PhysicalKeyCandidate(
                            physical_key_id=key.key_id,
                            model_key_id=key.model_key_id,
                            label=key.label,
                            rank=rank,
                            distance_reference=distance,
                            nearest_sample_index=nearest_index,
                            spatial_weight=spatial_weight,
                            probability=probability,
                            direct=direct,
                        )
                    )
                direct_key = next(
                    (
                        candidate.physical_key_id
                        for candidate in candidates
                        if candidate.direct
                    ),
                    None,
                )
                predictions.append(
                    FingertipKeyPrediction(
                        bubble_id=f"{hand_index}:{finger}",
                        hand_index=hand_index,
                        handedness=physical_hand,
                        mediapipe_handedness=mediapipe_hand,
                        finger=finger,
                        confidence=point_confidence,
                        image_x=landmark.x,
                        image_y=landmark.y,
                        reference_x=reference_x,
                        reference_y=reference_y,
                        pose_confidence=pose.confidence,
                        candidates=tuple(candidates),
                        direct_physical_key_id=direct_key,
                    )
                )

        return KeyCandidateResult(
            source_id=frame.source_id,
            frame_id=frame.frame_id,
            acquired_at_ns=frame.acquired_at_ns,
            status="ready",
            layout_id=self.contact_map.layout_id,
            layout_revision=self.contact_map.layout_revision,
            anchor_revision=self.contact_map.anchor_revision,
            contact_map_revision=self.contact_map.revision,
            pose_status=pose.status,
            pose_confidence=pose.confidence,
            predictions=tuple(predictions),
        )

    def map(
        self,
        frame: FramePacket,
        hands: HandTrackingResult,
        pose: KeyboardPoseEstimate,
    ) -> KeyCandidateResult:
        """Pipeline-friendly alias for :meth:`evaluate`."""

        return self.evaluate(frame, hands, pose)


ContactKeyMapper = ContactMapEvaluator
ContactKeyMapperConfig = ContactMapEvaluatorConfig


__all__ = [
    "ContactMapEvaluator",
    "ContactMapEvaluatorConfig",
    "ContactKeyMapper",
    "ContactKeyMapperConfig",
    "FINGERTIP_LANDMARKS",
    "FingertipKeyPrediction",
    "KeyCandidateError",
    "KeyCandidateResult",
    "PhysicalKeyCandidate",
]
