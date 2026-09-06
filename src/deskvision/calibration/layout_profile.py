"""Production keyboard layout-profile contract.

The profile is the user's editable key inventory and nominal drawing geometry.
It does not claim to measure physical key centres; physical contact positions
belong to :mod:`deskvision.calibration.contact_map`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import math
from pathlib import Path
from typing import Any

from ._storage import atomic_write_json, canonical_hash, read_json_object


LAYOUT_PROFILE_SCHEMA_VERSION = "keyboard-layout-map-0.1"
LEGACY_LAYOUT_PROFILE_SCHEMA_VERSIONS = frozenset(
    {"keyboard-layout-map-demo-0.1"}
)
SUPPORTED_LAYOUT_PROFILE_SCHEMA_VERSIONS = frozenset(
    {LAYOUT_PROFILE_SCHEMA_VERSION, *LEGACY_LAYOUT_PROFILE_SCHEMA_VERSIONS}
)

WIDE_KEY_IDS = frozenset(
    {
        "space",
        "left_shift",
        "right_shift",
        "enter",
        "backspace",
        "tab",
        "caps_lock",
        "left_control",
        "right_control",
    }
)


class LayoutProfileError(ValueError):
    """A layout-profile payload violates the production contract."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _non_empty_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise LayoutProfileError(f"{field} must be a non-empty string")
    return value


def _finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LayoutProfileError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise LayoutProfileError(f"{field} must be finite")
    return number


@dataclass(frozen=True, slots=True)
class KeyboardKey:
    """One logical/physical key and its nominal drawing rectangle."""

    key_id: str
    label: str
    browser_code: str
    physical_key_id: str
    model_key_id: str
    row: int
    x_units: float
    y_units: float
    width_units: float
    height_units: float
    is_wide: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "key_id",
            "label",
            "physical_key_id",
            "model_key_id",
        ):
            if not getattr(self, field_name):
                raise LayoutProfileError(f"KeyboardKey.{field_name} cannot be empty")
        if isinstance(self.row, bool) or not isinstance(self.row, int):
            raise LayoutProfileError(f"{self.key_id}.row must be an integer")
        values = (self.x_units, self.y_units, self.width_units, self.height_units)
        if not all(math.isfinite(value) for value in values):
            raise LayoutProfileError(f"{self.key_id} geometry must be finite")
        if self.x_units < 0 or self.y_units < 0:
            raise LayoutProfileError(f"{self.key_id} coordinates must be non-negative")
        if self.width_units <= 0 or self.height_units <= 0:
            raise LayoutProfileError(f"{self.key_id} width/height must be positive")

    def to_dict(self) -> dict[str, object]:
        return {
            "key_id": self.key_id,
            "label": self.label,
            "browser_code": self.browser_code,
            "physical_key_id": self.physical_key_id,
            "model_key_id": self.model_key_id,
            "row": self.row,
            "x_units": self.x_units,
            "y_units": self.y_units,
            "width_units": self.width_units,
            "height_units": self.height_units,
            "is_wide": self.is_wide,
        }


@dataclass(frozen=True, slots=True)
class MarkerAssignment:
    """A marker-to-key assignment used while registering a keyboard."""

    marker_id: int
    key_id: str
    role: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.marker_id, bool) or not isinstance(self.marker_id, int):
            raise LayoutProfileError("marker_id must be an integer")
        if self.marker_id < 0:
            raise LayoutProfileError("marker_id must be non-negative")
        if not self.key_id:
            raise LayoutProfileError("marker key_id cannot be empty")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "marker_id": self.marker_id,
            "key_id": self.key_id,
        }
        if self.role:
            result["role"] = self.role
        return result


@dataclass(frozen=True, slots=True)
class KeyboardLayoutProfile:
    """Validated user-authored key inventory and nominal visualization map."""

    layout_id: str
    layout_name: str
    source_layout: str
    unit: str
    row_step_units: float
    keys: tuple[KeyboardKey, ...]
    anchors: tuple[MarkerAssignment, ...]
    calibration_order: tuple[str, ...]
    created_at: str | None = None
    updated_at: str | None = None
    schema_version: str = LAYOUT_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != LAYOUT_PROFILE_SCHEMA_VERSION:
            raise LayoutProfileError(
                f"schema_version must be {LAYOUT_PROFILE_SCHEMA_VERSION}"
            )
        for field_name in ("layout_id", "layout_name", "unit"):
            if not getattr(self, field_name):
                raise LayoutProfileError(f"{field_name} cannot be empty")
        if not math.isfinite(self.row_step_units) or self.row_step_units <= 0:
            raise LayoutProfileError("row_step_units must be positive and finite")
        if not self.keys:
            raise LayoutProfileError("keys must not be empty")

        key_ids = tuple(key.key_id for key in self.keys)
        physical_ids = tuple(key.physical_key_id for key in self.keys)
        if len(key_ids) != len(set(key_ids)):
            raise LayoutProfileError("keys contain duplicate key_id values")
        if len(physical_ids) != len(set(physical_ids)):
            raise LayoutProfileError("keys contain duplicate physical_key_id values")
        if len(self.calibration_order) != len(set(self.calibration_order)):
            raise LayoutProfileError("calibration_order must not contain duplicates")
        if set(self.calibration_order) != set(key_ids):
            raise LayoutProfileError(
                "calibration_order must contain every key_id exactly once"
            )

        marker_ids = tuple(anchor.marker_id for anchor in self.anchors)
        marker_keys = tuple(anchor.key_id for anchor in self.anchors)
        if len(marker_ids) != len(set(marker_ids)):
            raise LayoutProfileError("anchors contain duplicate marker_id values")
        if len(marker_keys) != len(set(marker_keys)):
            raise LayoutProfileError("anchors must reference different keys")
        unknown_anchor_keys = sorted(set(marker_keys) - set(key_ids))
        if unknown_anchor_keys:
            raise LayoutProfileError(
                "anchors reference unknown keys: " + ", ".join(unknown_anchor_keys)
            )

    @property
    def key_count(self) -> int:
        return len(self.keys)

    @property
    def keyboard_width_units(self) -> float:
        return max(key.x_units + key.width_units for key in self.keys)

    @property
    def keyboard_height_units(self) -> float:
        return max(key.y_units + key.height_units for key in self.keys)

    @property
    def content_hash(self) -> str:
        return canonical_hash(self.to_dict())

    @property
    def inventory_revision(self) -> str:
        # Local import avoids making layout parsing depend on contact-map code.
        from .contact_map import build_layout_inventory

        return build_layout_inventory(self).revision

    def key(self, key_id: str) -> KeyboardKey:
        try:
            return next(key for key in self.keys if key.key_id == key_id)
        except StopIteration as exc:
            raise KeyError(key_id) from exc

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": LAYOUT_PROFILE_SCHEMA_VERSION,
            "layout_id": self.layout_id,
            "layout_name": self.layout_name,
            "source_layout": self.source_layout,
            "unit": self.unit,
            "row_step_units": self.row_step_units,
            "key_count": self.key_count,
            "keyboard_width_units": self.keyboard_width_units,
            "keyboard_height_units": self.keyboard_height_units,
            "anchors": [anchor.to_dict() for anchor in self.anchors],
            "calibration_order": list(self.calibration_order),
            "keys": [key.to_dict() for key in self.keys],
        }
        if self.created_at:
            payload["created_at"] = self.created_at
        if self.updated_at:
            payload["updated_at"] = self.updated_at
        return payload


def layout_profile_from_dict(payload: Mapping[str, object]) -> KeyboardLayoutProfile:
    """Validate and normalize a production or legacy Demo 0.1 profile."""
    schema_version = payload.get("schema_version")
    if schema_version not in SUPPORTED_LAYOUT_PROFILE_SCHEMA_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_LAYOUT_PROFILE_SCHEMA_VERSIONS))
        raise LayoutProfileError(f"unsupported schema_version; expected one of: {supported}")

    layout_id = _non_empty_string(payload.get("layout_id"), field="layout_id")
    layout_name = payload.get("layout_name", layout_id)
    source_layout = payload.get("source_layout", "user")
    unit = payload.get("unit", "keyboard_1u")
    if not isinstance(layout_name, str) or not isinstance(source_layout, str):
        raise LayoutProfileError("layout_name and source_layout must be strings")
    unit = _non_empty_string(unit, field="unit")
    row_step = _finite_number(
        payload.get("row_step_units", 1.0), field="row_step_units"
    )
    if row_step <= 0:
        raise LayoutProfileError("row_step_units must be positive")

    raw_keys = payload.get("keys")
    if not isinstance(raw_keys, Sequence) or isinstance(raw_keys, (str, bytes)):
        raise LayoutProfileError("keys must be a list")
    keys: list[KeyboardKey] = []
    for index, raw in enumerate(raw_keys):
        if not isinstance(raw, Mapping):
            raise LayoutProfileError(f"keys[{index}] must be an object")
        key_id = _non_empty_string(raw.get("key_id"), field=f"keys[{index}].key_id")
        label = raw.get("label", key_id)
        browser_code = raw.get("browser_code", "")
        physical_key_id = raw.get("physical_key_id", key_id)
        model_key_id = raw.get("model_key_id", key_id)
        if not all(
            isinstance(value, str)
            for value in (label, browser_code, physical_key_id, model_key_id)
        ):
            raise LayoutProfileError(
                f"{key_id} label/browser_code/physical_key_id/model_key_id must be strings"
            )
        _non_empty_string(physical_key_id, field=f"{key_id}.physical_key_id")
        _non_empty_string(model_key_id, field=f"{key_id}.model_key_id")
        row = raw.get("row", index)
        if isinstance(row, bool) or not isinstance(row, int):
            raise LayoutProfileError(f"{key_id}.row must be an integer")
        x_units = _finite_number(raw.get("x_units", 0.0), field=f"{key_id}.x_units")
        y_units = _finite_number(raw.get("y_units", 0.0), field=f"{key_id}.y_units")
        width_units = _finite_number(
            raw.get("width_units", 1.0), field=f"{key_id}.width_units"
        )
        height_units = _finite_number(
            raw.get("height_units", 1.0), field=f"{key_id}.height_units"
        )
        explicit_wide = raw.get("is_wide")
        if explicit_wide is None:
            is_wide = key_id in WIDE_KEY_IDS
        elif isinstance(explicit_wide, bool):
            is_wide = explicit_wide
        else:
            raise LayoutProfileError(f"{key_id}.is_wide must be boolean")
        keys.append(
            KeyboardKey(
                key_id=key_id,
                label=label,
                browser_code=browser_code,
                physical_key_id=physical_key_id,
                model_key_id=model_key_id,
                row=row,
                x_units=round(x_units, 6),
                y_units=round(y_units, 6),
                width_units=round(width_units, 6),
                height_units=round(height_units, 6),
                is_wide=is_wide,
            )
        )

    raw_order = payload.get("calibration_order")
    if raw_order is None:
        calibration_order = tuple(key.key_id for key in keys)
    elif isinstance(raw_order, Sequence) and not isinstance(raw_order, (str, bytes)):
        if not all(isinstance(value, str) for value in raw_order):
            raise LayoutProfileError("calibration_order must contain key IDs")
        calibration_order = tuple(raw_order)
    else:
        raise LayoutProfileError("calibration_order must be a list of key IDs")

    raw_anchors = payload.get("anchors", ())
    if not isinstance(raw_anchors, Sequence) or isinstance(raw_anchors, (str, bytes)):
        raise LayoutProfileError("anchors must be a list")
    anchors: list[MarkerAssignment] = []
    for index, raw in enumerate(raw_anchors):
        if not isinstance(raw, Mapping):
            raise LayoutProfileError(f"anchors[{index}] must be an object")
        marker_id = raw.get("marker_id")
        if isinstance(marker_id, bool) or not isinstance(marker_id, int):
            raise LayoutProfileError(f"anchors[{index}].marker_id must be an integer")
        key_id = _non_empty_string(
            raw.get("key_id"), field=f"anchors[{index}].key_id"
        )
        role = raw.get("role", "")
        if not isinstance(role, str):
            raise LayoutProfileError(f"anchors[{index}].role must be a string")
        anchors.append(MarkerAssignment(marker_id, key_id, role))

    created_at = payload.get("created_at")
    updated_at = payload.get("updated_at")
    if created_at is not None and not isinstance(created_at, str):
        raise LayoutProfileError("created_at must be a string when present")
    if updated_at is not None and not isinstance(updated_at, str):
        raise LayoutProfileError("updated_at must be a string when present")

    return KeyboardLayoutProfile(
        layout_id=layout_id,
        layout_name=layout_name,
        source_layout=source_layout,
        unit=unit,
        row_step_units=row_step,
        keys=tuple(keys),
        anchors=tuple(anchors),
        calibration_order=calibration_order,
        created_at=created_at,
        updated_at=updated_at,
    )


def validate_layout_profile(
    payload: KeyboardLayoutProfile | Mapping[str, object],
) -> KeyboardLayoutProfile:
    if isinstance(payload, KeyboardLayoutProfile):
        # Reparse to assert the serialized representation is also valid.
        return layout_profile_from_dict(payload.to_dict())
    return layout_profile_from_dict(payload)


def load_layout_profile(path: Path) -> KeyboardLayoutProfile:
    payload = read_json_object(
        Path(path),
        artifact_name="keyboard layout profile",
        error_factory=LayoutProfileError,
    )
    return layout_profile_from_dict(payload)


def save_layout_profile(
    path: Path,
    profile: KeyboardLayoutProfile | Mapping[str, object],
    *,
    timestamp: str | None = None,
) -> KeyboardLayoutProfile:
    """Validate and atomically save a production-owned layout profile."""
    path = Path(path)
    normalized = validate_layout_profile(profile)
    created_at = normalized.created_at
    if created_at is None and path.exists():
        try:
            created_at = load_layout_profile(path).created_at
        except LayoutProfileError:
            created_at = None
    now = timestamp or _now_iso()
    persisted = replace(
        normalized,
        created_at=created_at or now,
        updated_at=now,
    )
    atomic_write_json(path, persisted.to_dict())
    return persisted


__all__ = [
    "LAYOUT_PROFILE_SCHEMA_VERSION",
    "LEGACY_LAYOUT_PROFILE_SCHEMA_VERSIONS",
    "KeyboardKey",
    "KeyboardLayoutProfile",
    "LayoutProfileError",
    "MarkerAssignment",
    "layout_profile_from_dict",
    "load_layout_profile",
    "save_layout_profile",
    "validate_layout_profile",
]
