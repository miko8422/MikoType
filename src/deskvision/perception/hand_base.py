"""Production hand-tracking contracts shared by perception and transport."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol, Sequence

from deskvision.core.models import FramePacket


HAND_LANDMARK_COUNT = 21


class HandTrackerUnavailableError(RuntimeError):
    """A hand tracker cannot start because its runtime or model is unavailable."""


class HandTrackerInitializationError(RuntimeError):
    """A present hand-tracking runtime failed while being initialized."""


@dataclass(frozen=True, slots=True)
class HandLandmark:
    """One hand landmark in normalized image or metric world coordinates."""

    x: float
    y: float
    z: float = 0.0
    score: float = 1.0

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.x, self.y, self.z, self.score)):
            raise ValueError("hand landmark values must be finite")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("hand landmark score must be between 0 and 1")

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z, "score": self.score}


@dataclass(frozen=True, slots=True)
class DetectedHand:
    """One detected hand with the MediaPipe-standard 21-landmark topology."""

    handedness: str
    score: float
    landmarks: tuple[HandLandmark, ...]
    world_landmarks: tuple[HandLandmark, ...] = ()

    def __post_init__(self) -> None:
        if self.handedness not in {"left", "right", "unknown"}:
            raise ValueError("handedness must be left, right, or unknown")
        if not math.isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            raise ValueError("detected-hand score must be between 0 and 1")
        if len(self.landmarks) != HAND_LANDMARK_COUNT:
            raise ValueError(f"a detected hand must contain {HAND_LANDMARK_COUNT} landmarks")
        if self.world_landmarks and len(self.world_landmarks) != HAND_LANDMARK_COUNT:
            raise ValueError(
                f"world landmarks must be empty or contain {HAND_LANDMARK_COUNT} landmarks"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "handedness": self.handedness,
            "score": self.score,
            "landmarks": [landmark.to_dict() for landmark in self.landmarks],
            "world_landmarks": [
                landmark.to_dict() for landmark in self.world_landmarks
            ],
        }


@dataclass(frozen=True, slots=True)
class HandTrackingResult:
    """Frame-correlated, JSON-ready output from a production hand tracker."""

    model_id: str
    source_id: str
    frame_id: int
    acquired_at_ns: int
    inference_started_ns: int
    inference_completed_ns: int
    hands: tuple[DetectedHand, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("model_id must be non-empty")
        if self.inference_completed_ns < self.inference_started_ns:
            raise ValueError("inference completion cannot precede its start")

    @property
    def latency_ms(self) -> float:
        return (self.inference_completed_ns - self.inference_started_ns) / 1_000_000.0

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "hand-tracking-0.1",
            "model_id": self.model_id,
            "source_id": self.source_id,
            "frame_id": self.frame_id,
            "acquired_at_ns": self.acquired_at_ns,
            "inference_started_ns": self.inference_started_ns,
            "inference_completed_ns": self.inference_completed_ns,
            "latency_ms": self.latency_ms,
            "hands": [hand.to_dict() for hand in self.hands],
        }

    def scene_hands(self) -> tuple[dict[str, object], ...]:
        """Return the transport-safe value for ``SceneState.hands``."""

        return tuple(hand.to_dict() for hand in self.hands)


class HandTracker(Protocol):
    @property
    def model_id(self) -> str: ...

    def track(self, frame: FramePacket) -> HandTrackingResult: ...

    def close(self) -> None: ...


def ensure_landmark_sequence(
    values: Sequence[HandLandmark],
) -> tuple[HandLandmark, ...]:
    landmarks = tuple(values)
    if len(landmarks) != HAND_LANDMARK_COUNT:
        raise ValueError(f"expected {HAND_LANDMARK_COUNT} hand landmarks")
    return landmarks
