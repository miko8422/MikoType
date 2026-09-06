"""Measured ArUco anchor-reference artifact contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from ._storage import atomic_write_json, canonical_hash, read_json_object

if TYPE_CHECKING:
    from .contact_map import LayoutInventory
    from .layout_profile import KeyboardLayoutProfile


Point = tuple[float, float]
Corners = tuple[Point, Point, Point, Point]

ANCHOR_REFERENCE_SCHEMA_VERSION = "keyboard-anchor-reference-0.1"


class AnchorReferenceError(ValueError):
    """An anchor-reference payload or registration is invalid."""


def _corners(value: object, *, field: str) -> Corners:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise AnchorReferenceError(f"{field} must contain four ordered corners")
    points: list[Point] = []
    for index, point in enumerate(value):
        if (
            not isinstance(point, Sequence)
            or isinstance(point, (str, bytes))
            or len(point) != 2
        ):
            raise AnchorReferenceError(f"{field}[{index}] must contain x and y")
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError) as exc:
            raise AnchorReferenceError(f"{field}[{index}] must be numeric") from exc
        if not math.isfinite(x) or not math.isfinite(y):
            raise AnchorReferenceError(f"{field}[{index}] must be finite")
        points.append((x, y))
    array = np.asarray(points, dtype=np.float32)
    if not cv2.isContourConvex(array) or abs(float(cv2.contourArea(array))) <= 1e-8:
        raise AnchorReferenceError(f"{field} cannot be degenerate or non-convex")
    return (points[0], points[1], points[2], points[3])


@dataclass(frozen=True, slots=True)
class AnchorDefinition:
    marker_id: int
    key_id: str
    corners_reference: Corners

    def __post_init__(self) -> None:
        if isinstance(self.marker_id, bool) or not isinstance(self.marker_id, int):
            raise AnchorReferenceError("marker_id must be an integer")
        if self.marker_id < 0:
            raise AnchorReferenceError("marker_id must be non-negative")
        if not self.key_id:
            raise AnchorReferenceError("anchor key_id cannot be empty")
        parsed = _corners(self.corners_reference, field=f"anchor {self.marker_id}")
        object.__setattr__(self, "corners_reference", parsed)

    def to_dict(self) -> dict[str, object]:
        return {
            "marker_id": self.marker_id,
            "key_id": self.key_id,
            "corners_reference": [list(point) for point in self.corners_reference],
        }


@dataclass(frozen=True, slots=True)
class AnchorReference:
    """Stable physical marker geometry in reference-marker-side units."""

    reference_marker_id: int
    anchors: tuple[AnchorDefinition, ...]
    revision: str
    coordinate_unit: str = "reference_marker_side"

    def __post_init__(self) -> None:
        if len(self.anchors) < 2:
            raise AnchorReferenceError("an anchor reference requires at least two markers")
        marker_ids = tuple(anchor.marker_id for anchor in self.anchors)
        key_ids = tuple(anchor.key_id for anchor in self.anchors)
        if len(marker_ids) != len(set(marker_ids)):
            raise AnchorReferenceError("anchor reference contains duplicate marker IDs")
        if len(key_ids) != len(set(key_ids)):
            raise AnchorReferenceError("anchor reference contains duplicate key IDs")
        if self.reference_marker_id not in marker_ids:
            raise AnchorReferenceError("reference marker is not present in the anchor set")
        if not self.coordinate_unit:
            raise AnchorReferenceError("coordinate_unit cannot be empty")
        expected = anchor_reference_revision(
            self.reference_marker_id,
            self.anchors,
            self.coordinate_unit,
        )
        if self.revision != expected:
            raise AnchorReferenceError("anchor revision does not match its contents")

    @property
    def marker_ids(self) -> tuple[int, ...]:
        return tuple(anchor.marker_id for anchor in self.anchors)

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(anchor.key_id for anchor in self.anchors)

    @property
    def reference_quad(self) -> Corners:
        points = tuple(point for anchor in self.anchors for point in anchor.corners_reference)
        left = min(point[0] for point in points)
        right = max(point[0] for point in points)
        top = min(point[1] for point in points)
        bottom = max(point[1] for point in points)
        if right - left <= 1e-8 or bottom - top <= 1e-8:
            raise AnchorReferenceError("anchor reference bounds are degenerate")
        return ((left, top), (right, top), (right, bottom), (left, bottom))

    def anchor(self, marker_id: int) -> AnchorDefinition:
        try:
            return next(anchor for anchor in self.anchors if anchor.marker_id == marker_id)
        except StopIteration as exc:
            raise KeyError(marker_id) from exc

    def marker_ids_for_keys(self, key_ids: Sequence[str]) -> tuple[int, ...]:
        wanted = set(key_ids)
        return tuple(anchor.marker_id for anchor in self.anchors if anchor.key_id in wanted)

    def validate_inventory(self, inventory: "LayoutInventory") -> None:
        unknown = sorted(set(self.key_ids) - set(inventory.key_ids))
        if unknown:
            raise AnchorReferenceError(
                "anchor reference contains keys outside the active inventory: "
                + ", ".join(unknown)
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": ANCHOR_REFERENCE_SCHEMA_VERSION,
            "reference_marker_id": self.reference_marker_id,
            "coordinate_unit": self.coordinate_unit,
            "revision": self.revision,
            "anchors": [anchor.to_dict() for anchor in self.anchors],
        }


def anchor_reference_revision(
    reference_marker_id: int,
    anchors: Sequence[AnchorDefinition],
    coordinate_unit: str = "reference_marker_side",
) -> str:
    return canonical_hash(
        {
            "schema_version": ANCHOR_REFERENCE_SCHEMA_VERSION,
            "reference_marker_id": reference_marker_id,
            "coordinate_unit": coordinate_unit,
            "anchors": [anchor.to_dict() for anchor in anchors],
        }
    )


def create_anchor_reference(
    observations: Mapping[int, Sequence[Sequence[object]]],
    marker_key_ids: Mapping[int, str],
    *,
    reference_marker_id: int = 0,
) -> AnchorReference:
    """Measure all supplied markers in the selected marker's unit-square plane."""
    if len(marker_key_ids) < 2:
        raise AnchorReferenceError("at least two marker-to-key assignments are required")
    normalized_assignments: dict[int, str] = {}
    for raw_marker_id, raw_key_id in marker_key_ids.items():
        if isinstance(raw_marker_id, bool) or not isinstance(raw_marker_id, int):
            raise AnchorReferenceError("marker IDs must be integers")
        if raw_marker_id < 0:
            raise AnchorReferenceError("marker IDs must be non-negative")
        if not isinstance(raw_key_id, str) or not raw_key_id:
            raise AnchorReferenceError("every marker must reference a non-empty key_id")
        normalized_assignments[raw_marker_id] = raw_key_id
    if len(set(normalized_assignments.values())) != len(normalized_assignments):
        raise AnchorReferenceError("each marker must be attached to a different key")
    if reference_marker_id not in normalized_assignments:
        raise AnchorReferenceError("reference_marker_id must have a key assignment")
    missing = sorted(set(normalized_assignments) - set(observations))
    if missing:
        raise AnchorReferenceError(f"registration is missing assigned markers: {missing}")

    parsed = {
        marker_id: _corners(observations[marker_id], field=f"marker {marker_id}")
        for marker_id in normalized_assignments
    }
    canonical_marker = np.asarray(
        ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(
        np.asarray(parsed[reference_marker_id], dtype=np.float32),
        canonical_marker,
    )
    if not np.isfinite(transform).all():
        raise AnchorReferenceError("registration transform is not finite")

    anchors: list[AnchorDefinition] = []
    for marker_id in sorted(normalized_assignments):
        points = np.asarray(parsed[marker_id], dtype=np.float64).reshape(1, 4, 2)
        projected = cv2.perspectiveTransform(points, transform)[0]
        if not np.isfinite(projected).all():
            raise AnchorReferenceError("registration projected a marker to infinity")
        corners: Corners = tuple(
            (round(float(x), 9), round(float(y), 9)) for x, y in projected
        )  # type: ignore[assignment]
        anchors.append(
            AnchorDefinition(marker_id, normalized_assignments[marker_id], corners)
        )
    revision = anchor_reference_revision(reference_marker_id, anchors)
    return AnchorReference(reference_marker_id, tuple(anchors), revision)


def marker_key_ids_from_profile(
    profile: "KeyboardLayoutProfile | Mapping[str, object]",
) -> dict[int, str]:
    """Extract marker assignments without using nominal key geometry."""
    if hasattr(profile, "anchors") and not isinstance(profile, Mapping):
        raw_anchors: object = getattr(profile, "anchors")
    else:
        raw_anchors = profile.get("anchors")
    if not isinstance(raw_anchors, Sequence) or isinstance(raw_anchors, (str, bytes)):
        raise AnchorReferenceError("profile anchors must be a list")
    assignments: dict[int, str] = {}
    for index, raw in enumerate(raw_anchors):
        if hasattr(raw, "marker_id") and hasattr(raw, "key_id"):
            marker_id = getattr(raw, "marker_id")
            key_id = getattr(raw, "key_id")
        elif isinstance(raw, Mapping):
            marker_id = raw.get("marker_id")
            key_id = raw.get("key_id")
        else:
            raise AnchorReferenceError(f"profile anchors[{index}] must be an object")
        if isinstance(marker_id, bool) or not isinstance(marker_id, int):
            raise AnchorReferenceError(f"profile anchors[{index}].marker_id must be an integer")
        if not isinstance(key_id, str) or not key_id:
            raise AnchorReferenceError(f"profile anchors[{index}].key_id is invalid")
        if marker_id in assignments:
            raise AnchorReferenceError("profile contains a duplicate marker ID")
        assignments[marker_id] = key_id
    if len(assignments) < 2 or len(set(assignments.values())) != len(assignments):
        raise AnchorReferenceError("profile must assign at least two markers to unique keys")
    return assignments


def anchor_reference_from_dict(payload: Mapping[str, object]) -> AnchorReference:
    if payload.get("schema_version") != ANCHOR_REFERENCE_SCHEMA_VERSION:
        raise AnchorReferenceError(
            f"schema_version must be {ANCHOR_REFERENCE_SCHEMA_VERSION}"
        )
    raw_anchors = payload.get("anchors")
    if not isinstance(raw_anchors, Sequence) or isinstance(raw_anchors, (str, bytes)):
        raise AnchorReferenceError("anchors must be a list")
    anchors: list[AnchorDefinition] = []
    for index, raw in enumerate(raw_anchors):
        if not isinstance(raw, Mapping):
            raise AnchorReferenceError(f"anchors[{index}] must be an object")
        marker_id = raw.get("marker_id")
        key_id = raw.get("key_id")
        if isinstance(marker_id, bool) or not isinstance(marker_id, int):
            raise AnchorReferenceError(f"anchors[{index}].marker_id must be an integer")
        if not isinstance(key_id, str) or not key_id:
            raise AnchorReferenceError(f"anchors[{index}].key_id is invalid")
        anchors.append(
            AnchorDefinition(
                marker_id,
                key_id,
                _corners(raw.get("corners_reference"), field=f"anchor {marker_id}"),
            )
        )
    anchors.sort(key=lambda anchor: anchor.marker_id)
    reference_marker_id = payload.get("reference_marker_id")
    coordinate_unit = payload.get("coordinate_unit")
    revision = payload.get("revision")
    if isinstance(reference_marker_id, bool) or not isinstance(reference_marker_id, int):
        raise AnchorReferenceError("reference_marker_id must be an integer")
    if not isinstance(coordinate_unit, str) or not coordinate_unit:
        raise AnchorReferenceError("coordinate_unit must be a non-empty string")
    if not isinstance(revision, str) or not revision:
        raise AnchorReferenceError("revision must be a non-empty string")
    return AnchorReference(
        reference_marker_id,
        tuple(anchors),
        revision,
        coordinate_unit,
    )


def load_anchor_reference(
    path: Path,
    *,
    inventory: "LayoutInventory | None" = None,
) -> AnchorReference:
    payload = read_json_object(
        Path(path),
        artifact_name="anchor reference",
        error_factory=AnchorReferenceError,
    )
    reference = anchor_reference_from_dict(payload)
    if inventory is not None:
        reference.validate_inventory(inventory)
    return reference


def save_anchor_reference(path: Path, reference: AnchorReference) -> None:
    # Reconstruct to ensure callers cannot persist a manually forged object.
    validated = anchor_reference_from_dict(reference.to_dict())
    atomic_write_json(Path(path), validated.to_dict())


__all__ = [
    "ANCHOR_REFERENCE_SCHEMA_VERSION",
    "AnchorDefinition",
    "AnchorReference",
    "AnchorReferenceError",
    "Corners",
    "Point",
    "anchor_reference_from_dict",
    "anchor_reference_revision",
    "create_anchor_reference",
    "load_anchor_reference",
    "marker_key_ids_from_profile",
    "save_anchor_reference",
]
