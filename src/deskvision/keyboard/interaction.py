"""Reusable physical-key highlight and fingertip Bubble algorithms.

The module owns only deterministic geometry/strength calculations.  It does
not read cameras, artifacts, Demo modules, or transport state.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TYPE_CHECKING, Iterable, Sequence

from deskvision.perception.hand_base import HandTrackingResult

if TYPE_CHECKING:
    from deskvision.perception.key_candidates import KeyCandidateResult


FINGERTIP_LANDMARKS = (
    (4, "thumb"),
    (8, "index"),
    (12, "middle"),
    (16, "ring"),
    (20, "pinky"),
)


def _finite(value: float, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


@dataclass(frozen=True, slots=True)
class KeyboardPlane:
    """Normalized image rectangle occupied by the keyboard layout."""

    x: float = 0.04
    y: float = 0.46
    width: float = 0.92
    height: float = 0.50

    def __post_init__(self) -> None:
        x = _finite(self.x, field="keyboard plane x")
        y = _finite(self.y, field="keyboard plane y")
        width = _finite(self.width, field="keyboard plane width")
        height = _finite(self.height, field="keyboard plane height")
        if width <= 0 or height <= 0:
            raise ValueError("keyboard plane size must be positive")
        if x < 0 or y < 0 or x + width > 1 or y + height > 1:
            raise ValueError("keyboard plane must stay inside the normalized image")
        object.__setattr__(self, "x", x)
        object.__setattr__(self, "y", y)
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)


@dataclass(frozen=True, slots=True)
class KeyboardInteractionConfig:
    """Tunable Bubble and neighbor-falloff policy."""

    plane: KeyboardPlane = KeyboardPlane()
    bubble_radius_px: float = 26.0
    halo_radius_px: float = 58.0
    neighbor_strength: float = 0.38
    min_confidence: float = 0.35

    def __post_init__(self) -> None:
        bubble = _finite(self.bubble_radius_px, field="bubble radius")
        halo = _finite(self.halo_radius_px, field="halo radius")
        strength = _finite(self.neighbor_strength, field="neighbor strength")
        confidence = _finite(self.min_confidence, field="minimum confidence")
        if not 2 <= bubble <= 160:
            raise ValueError("bubble radius must be between 2 and 160 pixels")
        if not 0 <= halo <= 240:
            raise ValueError("halo radius must be between 0 and 240 pixels")
        if not 0 <= strength <= 1:
            raise ValueError("neighbor strength must be between 0 and 1")
        if not 0 <= confidence <= 1:
            raise ValueError("minimum confidence must be between 0 and 1")
        object.__setattr__(self, "bubble_radius_px", bubble)
        object.__setattr__(self, "halo_radius_px", halo)
        object.__setattr__(self, "neighbor_strength", strength)
        object.__setattr__(self, "min_confidence", confidence)


@dataclass(frozen=True, slots=True)
class KeyboardRect:
    """One physical key rectangle normalized within a :class:`KeyboardPlane`."""

    key_id: str
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if not isinstance(self.key_id, str) or not self.key_id.strip():
            raise ValueError("key_id must be a non-empty string")
        x = _finite(self.x, field=f"{self.key_id}.x")
        y = _finite(self.y, field=f"{self.key_id}.y")
        width = _finite(self.width, field=f"{self.key_id}.width")
        height = _finite(self.height, field=f"{self.key_id}.height")
        if width <= 0 or height <= 0:
            raise ValueError(f"{self.key_id} rectangle size must be positive")
        if x < 0 or y < 0 or x + width > 1 or y + height > 1:
            raise ValueError(
                f"{self.key_id} rectangle must stay inside the normalized keyboard"
            )
        object.__setattr__(self, "x", x)
        object.__setattr__(self, "y", y)
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)


@dataclass(frozen=True, slots=True)
class FingertipBubble:
    """One confidence-gated fingertip influence circle in normalized image space."""

    bubble_id: str
    hand_index: int
    handedness: str
    finger: str
    x: float
    y: float
    confidence: float
    radius_px: float

    def __post_init__(self) -> None:
        if not isinstance(self.bubble_id, str) or not self.bubble_id:
            raise ValueError("bubble_id must be non-empty")
        if isinstance(self.hand_index, bool) or not isinstance(self.hand_index, int):
            raise TypeError("hand_index must be an integer")
        if self.hand_index < 0:
            raise ValueError("hand_index must be non-negative")
        if not isinstance(self.finger, str) or not self.finger:
            raise ValueError("finger must be non-empty")
        x = _finite(self.x, field=f"{self.bubble_id}.x")
        y = _finite(self.y, field=f"{self.bubble_id}.y")
        confidence = _finite(self.confidence, field=f"{self.bubble_id}.confidence")
        radius = _finite(self.radius_px, field=f"{self.bubble_id}.radius_px")
        if not 0 <= confidence <= 1:
            raise ValueError("bubble confidence must be between 0 and 1")
        if radius <= 0:
            raise ValueError("bubble radius must be positive")
        object.__setattr__(self, "x", x)
        object.__setattr__(self, "y", y)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "radius_px", radius)


@dataclass(frozen=True, slots=True)
class KeyHighlight:
    """A highlight addressed exclusively by physical ``key_id``."""

    key_id: str
    intensity: float
    direct: bool = False
    contributors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.key_id, str) or not self.key_id.strip():
            raise ValueError("key_id must be a non-empty string")
        intensity = _finite(self.intensity, field=f"{self.key_id}.intensity")
        if not 0 <= intensity <= 1:
            raise ValueError("highlight intensity must be between 0 and 1")
        if not isinstance(self.direct, bool):
            raise TypeError("highlight direct flag must be boolean")
        if any(not isinstance(value, str) or not value for value in self.contributors):
            raise ValueError("highlight contributors must be non-empty strings")
        object.__setattr__(self, "intensity", intensity)
        object.__setattr__(self, "contributors", tuple(self.contributors))

    def to_dict(self) -> dict[str, object]:
        return {
            "key_id": self.key_id,
            "intensity": self.intensity,
            "direct": self.direct,
            "contributors": list(self.contributors),
        }


def fingertip_bubbles(
    tracking: HandTrackingResult,
    config: KeyboardInteractionConfig,
    *,
    mirror_x: bool = True,
) -> tuple[FingertipBubble, ...]:
    """Extract the five MediaPipe-standard fingertips from every valid hand."""

    output: list[FingertipBubble] = []
    for hand_index, hand in enumerate(tracking.hands):
        for landmark_index, finger in FINGERTIP_LANDMARKS:
            landmark = hand.landmarks[landmark_index]
            confidence = min(hand.score, landmark.score)
            if confidence < config.min_confidence:
                continue
            x = 1.0 - landmark.x if mirror_x else landmark.x
            output.append(
                FingertipBubble(
                    bubble_id=f"{hand_index}:{finger}",
                    hand_index=hand_index,
                    handedness=hand.handedness,
                    finger=finger,
                    x=x,
                    y=landmark.y,
                    confidence=confidence,
                    radius_px=config.bubble_radius_px,
                )
            )
    return tuple(output)


def aggregate_key_highlights(
    highlights: Iterable[KeyHighlight],
) -> tuple[KeyHighlight, ...]:
    """Merge repeated physical keys deterministically.

    The first occurrence fixes output order.  Intensity uses the maximum so a
    later weak candidate cannot dim a stronger one, ``direct`` uses logical OR,
    and contributor IDs are de-duplicated and sorted.
    """

    order: list[str] = []
    intensities: dict[str, float] = {}
    direct_flags: dict[str, bool] = {}
    contributors: dict[str, set[str]] = {}
    for highlight in highlights:
        key_id = highlight.key_id
        if key_id not in intensities:
            order.append(key_id)
            intensities[key_id] = highlight.intensity
            direct_flags[key_id] = highlight.direct
            contributors[key_id] = set(highlight.contributors)
            continue
        intensities[key_id] = max(intensities[key_id], highlight.intensity)
        direct_flags[key_id] = direct_flags[key_id] or highlight.direct
        contributors[key_id].update(highlight.contributors)
    return tuple(
        KeyHighlight(
            key_id=key_id,
            intensity=intensities[key_id],
            direct=direct_flags[key_id],
            contributors=tuple(sorted(contributors[key_id])),
        )
        for key_id in order
    )


def highlights_from_key_candidates(
    result: "KeyCandidateResult",
) -> tuple[KeyHighlight, ...]:
    """Convert ready physical-key predictions into deterministic highlights.

    Non-ready results intentionally produce no state, preventing stale pose,
    frame-mismatch, or artifact-mismatch predictions from remaining visible.
    The legacy ``model_key_id`` carried by calibration is never consulted.
    """

    if result.status != "ready" or not result.usable:
        return ()
    expanded: list[KeyHighlight] = []
    for prediction in result.predictions:
        for candidate in prediction.candidates:
            expanded.append(
                KeyHighlight(
                    key_id=candidate.physical_key_id,
                    intensity=candidate.probability,
                    direct=candidate.direct,
                    contributors=(prediction.bubble_id,),
                )
            )
    return aggregate_key_highlights(expanded)


def evaluate_bubble_highlights(
    keys: Sequence[KeyboardRect],
    bubbles: Sequence[FingertipBubble],
    *,
    frame_width: int,
    frame_height: int,
    config: KeyboardInteractionConfig,
) -> tuple[KeyHighlight, ...]:
    """Evaluate direct hits and squared neighbor falloff for physical keys."""

    if isinstance(frame_width, bool) or not isinstance(frame_width, int):
        raise TypeError("frame_width must be an integer")
    if isinstance(frame_height, bool) or not isinstance(frame_height, int):
        raise TypeError("frame_height must be an integer")
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("frame dimensions must be positive")

    plane = config.plane
    raw: list[KeyHighlight] = []
    for key in keys:
        left = (plane.x + key.x * plane.width) * frame_width
        top = (plane.y + key.y * plane.height) * frame_height
        right = left + key.width * plane.width * frame_width
        bottom = top + key.height * plane.height * frame_height
        best_intensity = 0.0
        direct = False
        contributors: set[str] = set()
        for bubble in bubbles:
            center_x = bubble.x * frame_width
            center_y = bubble.y * frame_height
            center_inside = left <= center_x <= right and top <= center_y <= bottom
            if center_inside:
                intensity = 1.0
                direct = True
            else:
                delta_x = max(left - center_x, 0.0, center_x - right)
                delta_y = max(top - center_y, 0.0, center_y - bottom)
                edge_distance = math.hypot(delta_x, delta_y)
                outside_bubble = max(0.0, edge_distance - bubble.radius_px)
                if config.halo_radius_px == 0 or outside_bubble >= config.halo_radius_px:
                    intensity = 0.0
                else:
                    falloff = 1.0 - outside_bubble / config.halo_radius_px
                    intensity = config.neighbor_strength * falloff * falloff
            if intensity > 0:
                contributors.add(bubble.bubble_id)
            best_intensity = max(best_intensity, intensity)
        raw.append(
            KeyHighlight(
                key_id=key.key_id,
                intensity=best_intensity,
                direct=direct,
                contributors=tuple(sorted(contributors)),
            )
        )
    return aggregate_key_highlights(raw)


__all__ = [
    "FINGERTIP_LANDMARKS",
    "FingertipBubble",
    "KeyHighlight",
    "KeyboardInteractionConfig",
    "KeyboardPlane",
    "KeyboardRect",
    "aggregate_key_highlights",
    "evaluate_bubble_highlights",
    "fingertip_bubbles",
    "highlights_from_key_candidates",
]
