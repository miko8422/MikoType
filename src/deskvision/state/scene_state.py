"""Versioned, transport-safe state produced by the production vision pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import time
from typing import Any, Mapping

from deskvision.perception.hand_base import DetectedHand


SCENE_STATE_SCHEMA_VERSION = "0.2"
KEY_CANDIDATE_SEMANTICS = "likely-contact-not-mechanical-keypress"


def _unit_interval(value: float, *, field_name: str) -> None:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")


@dataclass(frozen=True, slots=True)
class ArtifactRevisions:
    layout_content: str
    layout_inventory: str
    anchor: str
    contact_map: str
    model: str

    def __post_init__(self) -> None:
        if any(not value for value in asdict(self).values()):
            raise ValueError("all artifact revisions must be non-empty")


@dataclass(frozen=True, slots=True)
class KeyboardPoseState:
    status: str
    usable: bool
    confidence: float
    anchor_count: int
    detected_marker_ids: tuple[int, ...] = ()
    reference_to_image: tuple[float, ...] | None = None
    reprojection_rmse_px: float | None = None
    coast_age_ms: float = 0.0

    def __post_init__(self) -> None:
        if not self.status:
            raise ValueError("keyboard pose status must be non-empty")
        _unit_interval(self.confidence, field_name="keyboard pose confidence")
        if self.anchor_count < 0:
            raise ValueError("keyboard pose anchor_count cannot be negative")
        if self.reference_to_image is not None:
            if len(self.reference_to_image) != 9 or not all(
                math.isfinite(value) for value in self.reference_to_image
            ):
                raise ValueError("reference_to_image must contain nine finite values")
        if self.reprojection_rmse_px is not None and (
            not math.isfinite(self.reprojection_rmse_px)
            or self.reprojection_rmse_px < 0
        ):
            raise ValueError("reprojection_rmse_px must be finite and non-negative")
        if not math.isfinite(self.coast_age_ms) or self.coast_age_ms < 0:
            raise ValueError("coast_age_ms must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class KeyboardModelState:
    revision: str
    sha256: str
    uri: str
    manifest_uri: str
    key_count: int
    node_prefix: str = "key:"

    def __post_init__(self) -> None:
        if not all((self.revision, self.sha256, self.uri, self.manifest_uri)):
            raise ValueError("keyboard model metadata cannot be empty")
        if self.key_count <= 0:
            raise ValueError("keyboard model key_count must be positive")


@dataclass(frozen=True, slots=True)
class KeyCandidateState:
    physical_key_id: str
    model_node_id: str
    label: str
    rank: int
    distance: float
    probability: float
    direct: bool

    def __post_init__(self) -> None:
        if not self.physical_key_id or not self.model_node_id:
            raise ValueError("key candidate IDs must be non-empty")
        if self.rank <= 0:
            raise ValueError("key candidate rank must be positive")
        if not math.isfinite(self.distance) or self.distance < 0:
            raise ValueError("key candidate distance must be finite and non-negative")
        _unit_interval(self.probability, field_name="key candidate probability")


@dataclass(frozen=True, slots=True)
class FingertipState:
    bubble_id: str
    hand_index: int
    handedness: str
    finger: str
    confidence: float
    image_x: float
    image_y: float
    reference_x: float
    reference_y: float
    pose_confidence: float
    candidates: tuple[KeyCandidateState, ...] = ()

    def __post_init__(self) -> None:
        if not self.bubble_id or not self.finger:
            raise ValueError("fingertip identifiers must be non-empty")
        if self.hand_index < 0:
            raise ValueError("hand_index cannot be negative")
        if self.handedness not in {"left", "right", "unknown"}:
            raise ValueError("handedness must be left, right, or unknown")
        _unit_interval(self.confidence, field_name="fingertip confidence")
        _unit_interval(self.pose_confidence, field_name="fingertip pose confidence")
        if not all(
            math.isfinite(value)
            for value in (
                self.image_x,
                self.image_y,
                self.reference_x,
                self.reference_y,
            )
        ):
            raise ValueError("fingertip coordinates must be finite")


@dataclass(frozen=True, slots=True)
class KeyHighlightState:
    physical_key_id: str
    model_node_id: str
    label: str
    intensity: float
    direct: bool
    contributors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.physical_key_id or not self.model_node_id:
            raise ValueError("key highlight IDs must be non-empty")
        _unit_interval(self.intensity, field_name="key highlight intensity")


@dataclass(frozen=True, slots=True)
class KeyboardState:
    coordinate_space: str
    pose: KeyboardPoseState
    artifacts: ArtifactRevisions
    model: KeyboardModelState
    key_semantics: str = KEY_CANDIDATE_SEMANTICS

    def __post_init__(self) -> None:
        if not self.coordinate_space:
            raise ValueError("keyboard coordinate_space must be non-empty")
        if self.key_semantics != KEY_CANDIDATE_SEMANTICS:
            raise ValueError("unsupported keyboard key semantics")


@dataclass(frozen=True, slots=True)
class Diagnostics:
    capture_fps: float = 0.0
    frame_age_ms: float = 0.0
    hand_inference_ms: float = 0.0
    keyboard_inference_ms: float = 0.0
    perception_ms: float = 0.0
    status: str = "initializing"
    error: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "capture_fps",
            "frame_age_ms",
            "hand_inference_ms",
            "keyboard_inference_ms",
            "perception_ms",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"diagnostics {name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class SceneState:
    """One atomic same-frame result; it never embeds camera image bytes."""

    schema_version: str
    source_id: str
    source_frame_id: int
    captured_at_ns: int
    emitted_at_ns: int = field(default_factory=time.time_ns)
    keyboard: KeyboardState | None = None
    hands: tuple[DetectedHand | Mapping[str, Any], ...] = ()
    fingertips: tuple[FingertipState, ...] = ()
    key_highlights: tuple[KeyHighlightState, ...] = ()
    mouse: Mapping[str, Any] | None = None
    diagnostics: Diagnostics = field(default_factory=Diagnostics)

    def __post_init__(self) -> None:
        if self.schema_version != SCENE_STATE_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {SCENE_STATE_SCHEMA_VERSION}"
            )
        if not self.source_id:
            raise ValueError("source_id must be non-empty")
        if self.source_frame_id < 0:
            raise ValueError("source_frame_id cannot be negative")
        if self.captured_at_ns < 0 or self.emitted_at_ns < 0:
            raise ValueError("SceneState timestamps cannot be negative")

    @classmethod
    def empty(
        cls,
        *,
        source_id: str,
        source_frame_id: int,
        captured_at_ns: int,
        diagnostics: Diagnostics | None = None,
    ) -> "SceneState":
        return cls(
            schema_version=SCENE_STATE_SCHEMA_VERSION,
            source_id=source_id,
            source_frame_id=source_frame_id,
            captured_at_ns=captured_at_ns,
            diagnostics=diagnostics or Diagnostics(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready structure without raw frame data.

        ``hovered_keys`` remains as a compatibility alias during the 0.2
        transition. Its entries are identical to ``key_highlights``.
        """

        hands: list[dict[str, Any]] = []
        for hand in self.hands:
            if isinstance(hand, DetectedHand):
                hands.append(hand.to_dict())
            else:
                hands.append(dict(hand))
        keyboard = None if self.keyboard is None else asdict(self.keyboard)
        fingertips = [asdict(item) for item in self.fingertips]
        key_highlights = [asdict(item) for item in self.key_highlights]
        return {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "source_frame_id": self.source_frame_id,
            "captured_at_ns": self.captured_at_ns,
            "emitted_at_ns": self.emitted_at_ns,
            "keyboard": keyboard,
            "hands": hands,
            "fingertips": fingertips,
            "key_highlights": key_highlights,
            "hovered_keys": key_highlights,
            "mouse": None if self.mouse is None else dict(self.mouse),
            "diagnostics": asdict(self.diagnostics),
        }


__all__ = [
    "ArtifactRevisions",
    "Diagnostics",
    "FingertipState",
    "KEY_CANDIDATE_SEMANTICS",
    "KeyCandidateState",
    "KeyHighlightState",
    "KeyboardModelState",
    "KeyboardPoseState",
    "KeyboardState",
    "SCENE_STATE_SCHEMA_VERSION",
    "SceneState",
]
