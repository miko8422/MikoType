"""Production key-inventory, contact-map, and calibration-draft contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import math
from pathlib import Path
import statistics
from typing import TYPE_CHECKING

from ._storage import atomic_write_json, canonical_hash, read_json_object

if TYPE_CHECKING:
    from .layout_profile import KeyboardLayoutProfile


Point = tuple[float, float]

INVENTORY_SCHEMA_VERSION = "keyboard-layout-inventory-0.1"
CONTACT_MAP_SCHEMA_VERSION = "keyboard-contact-map-0.1"
DRAFT_SCHEMA_VERSION = "keyboard-contact-map-draft-0.1"
SAMPLES_PER_KEY = 5
COORDINATE_SYSTEM = "aruco-anchor-reference-2d"

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


class ContactMapError(ValueError):
    """A contact-map payload violates the production 0.1 contract."""


class RevisionMismatchError(ContactMapError):
    """Measured data belongs to another layout or anchor reference."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _point(value: object, *, field: str) -> Point:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise ContactMapError(f"{field} must contain x and y")
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError) as exc:
        raise ContactMapError(f"{field} must contain numeric x and y") from exc
    if not math.isfinite(x) or not math.isfinite(y):
        raise ContactMapError(f"{field} coordinates must be finite")
    return (x, y)


def median_point(samples: Sequence[Sequence[object]]) -> Point:
    points = tuple(_point(sample, field="sample") for sample in samples)
    if not points:
        raise ContactMapError("at least one sample is required")
    return (
        float(statistics.median(point[0] for point in points)),
        float(statistics.median(point[1] for point in points)),
    )


@dataclass(frozen=True, slots=True)
class LayoutKey:
    """The ordered key identity that invalidates contact calibration."""

    key_id: str
    label: str
    browser_code: str
    model_key_id: str
    is_wide: bool = False

    def __post_init__(self) -> None:
        if not self.key_id:
            raise ContactMapError("layout key_id cannot be empty")
        if not self.model_key_id:
            raise ContactMapError(f"{self.key_id}.model_key_id cannot be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "key_id": self.key_id,
            "label": self.label,
            "browser_code": self.browser_code,
            "model_key_id": self.model_key_id,
            "is_wide": self.is_wide,
        }


@dataclass(frozen=True, slots=True)
class LayoutInventory:
    """Calibration order/identity, intentionally excluding nominal geometry."""

    layout_id: str
    keys: tuple[LayoutKey, ...]
    revision: str

    def __post_init__(self) -> None:
        if not self.layout_id:
            raise ContactMapError("layout_id cannot be empty")
        if not self.keys:
            raise ContactMapError("layout inventory cannot be empty")
        ids = tuple(key.key_id for key in self.keys)
        if len(ids) != len(set(ids)):
            raise ContactMapError("layout inventory contains duplicate key IDs")
        if self.revision != inventory_revision(self.layout_id, self.keys):
            raise ContactMapError("layout inventory revision does not match its contents")

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(key.key_id for key in self.keys)

    def key(self, key_id: str) -> LayoutKey:
        try:
            return next(key for key in self.keys if key.key_id == key_id)
        except StopIteration as exc:
            raise KeyError(key_id) from exc

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": INVENTORY_SCHEMA_VERSION,
            "layout_id": self.layout_id,
            "revision": self.revision,
            "keys": [key.to_dict() for key in self.keys],
        }


def inventory_revision(layout_id: str, keys: Sequence[LayoutKey]) -> str:
    return canonical_hash(
        {
            "schema_version": INVENTORY_SCHEMA_VERSION,
            "layout_id": layout_id,
            "keys": [key.to_dict() for key in keys],
        }
    )


def _profile_fields(
    profile: "KeyboardLayoutProfile | Mapping[str, object]",
) -> tuple[object, object, object]:
    if isinstance(profile, Mapping):
        return profile.get("layout_id"), profile.get("keys"), profile.get("calibration_order")
    return profile.layout_id, profile.keys, profile.calibration_order


def build_layout_inventory(
    profile: "KeyboardLayoutProfile | Mapping[str, object]",
) -> LayoutInventory:
    """Extract the ordered identity contract from a layout profile.

    Nominal x/y/width/height are deliberately ignored. Moving a key in the
    visual editor therefore does not destroy measured physical calibration.
    """
    layout_id_raw, raw_keys, raw_order = _profile_fields(profile)
    if not isinstance(layout_id_raw, str) or not layout_id_raw:
        raise ContactMapError("layout profile must contain a non-empty layout_id")
    if not isinstance(raw_keys, Sequence) or isinstance(raw_keys, (str, bytes)):
        raise ContactMapError("layout profile keys must be an ordered list")

    keys_by_id: dict[str, LayoutKey] = {}
    original_order: list[str] = []
    for index, raw in enumerate(raw_keys):
        if isinstance(raw, Mapping):
            key_id = raw.get("key_id")
            label = raw.get("label", key_id)
            browser_code = raw.get("browser_code", "")
            model_key_id = raw.get("model_key_id", key_id)
            explicit_wide = raw.get("is_wide")
        else:
            key_id = getattr(raw, "key_id", None)
            label = getattr(raw, "label", key_id)
            browser_code = getattr(raw, "browser_code", "")
            model_key_id = getattr(raw, "model_key_id", key_id)
            explicit_wide = getattr(raw, "is_wide", None)
        if not isinstance(key_id, str) or not key_id:
            raise ContactMapError(f"keys[{index}].key_id must be a non-empty string")
        if not all(isinstance(value, str) for value in (label, browser_code, model_key_id)):
            raise ContactMapError(
                f"{key_id} label/browser_code/model_key_id must be strings"
            )
        if not model_key_id:
            raise ContactMapError(f"{key_id}.model_key_id cannot be empty")
        if explicit_wide is None:
            is_wide = key_id in WIDE_KEY_IDS
        elif isinstance(explicit_wide, bool):
            is_wide = explicit_wide
        else:
            raise ContactMapError(f"{key_id}.is_wide must be boolean")
        if key_id in keys_by_id:
            raise ContactMapError("layout inventory contains duplicate key IDs")
        keys_by_id[key_id] = LayoutKey(
            key_id,
            label,
            browser_code,
            model_key_id,
            is_wide,
        )
        original_order.append(key_id)

    if raw_order is None:
        order = original_order
    elif isinstance(raw_order, Sequence) and not isinstance(raw_order, (str, bytes)):
        if not all(isinstance(key_id, str) for key_id in raw_order):
            raise ContactMapError("calibration_order must contain only key IDs")
        order = list(raw_order)
        if len(order) != len(set(order)) or set(order) != set(keys_by_id):
            raise ContactMapError(
                "calibration_order must contain every inventory key exactly once"
            )
    else:
        raise ContactMapError("calibration_order must be a list of key IDs")

    keys = tuple(keys_by_id[key_id] for key_id in order)
    return LayoutInventory(layout_id_raw, keys, inventory_revision(layout_id_raw, keys))


@dataclass(frozen=True, slots=True)
class ContactKey:
    key_id: str
    label: str
    browser_code: str
    model_key_id: str
    is_wide: bool
    samples: tuple[Point, ...]
    center: Point

    def __post_init__(self) -> None:
        if not self.key_id or not self.model_key_id:
            raise ContactMapError("contact key IDs cannot be empty")
        if len(self.samples) != SAMPLES_PER_KEY:
            raise ContactMapError(
                f"{self.key_id} requires exactly {SAMPLES_PER_KEY} samples"
            )
        samples = tuple(_point(value, field=f"{self.key_id}.samples") for value in self.samples)
        center = _point(self.center, field=f"{self.key_id}.center")
        expected = median_point(samples)
        if not all(
            math.isclose(actual, wanted, abs_tol=1e-8)
            for actual, wanted in zip(center, expected, strict=True)
        ):
            raise ContactMapError(f"{self.key_id}.center is not the median of its samples")
        object.__setattr__(self, "samples", samples)
        object.__setattr__(self, "center", center)

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "browser_code": self.browser_code,
            "model_key_id": self.model_key_id,
            "is_wide": self.is_wide,
            "samples": [list(point) for point in self.samples],
            "center": list(self.center),
        }


def _contact_revision_payload(
    *,
    layout_id: str,
    layout_revision: str,
    anchor_revision: str,
    coordinate_system: str,
    samples_per_key: int,
    keys: Sequence[ContactKey],
) -> dict[str, object]:
    return {
        "schema_version": CONTACT_MAP_SCHEMA_VERSION,
        "layout_id": layout_id,
        "layout_revision": layout_revision,
        "anchor_revision": anchor_revision,
        "coordinate_system": coordinate_system,
        "samples_per_key": samples_per_key,
        "keys": {key.key_id: key.to_dict() for key in keys},
    }


@dataclass(frozen=True, slots=True)
class KeyboardContactMap:
    layout_id: str
    layout_revision: str
    anchor_revision: str
    keys: tuple[ContactKey, ...]
    created_at: str
    revision: str
    samples_per_key: int = SAMPLES_PER_KEY
    coordinate_system: str = COORDINATE_SYSTEM

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (
                self.layout_id,
                self.layout_revision,
                self.anchor_revision,
                self.created_at,
                self.revision,
                self.coordinate_system,
            )
        ):
            raise ContactMapError("contact map metadata contains an empty value")
        if self.samples_per_key != SAMPLES_PER_KEY:
            raise ContactMapError(f"samples_per_key must be {SAMPLES_PER_KEY}")
        if not self.keys:
            raise ContactMapError("contact map keys must not be empty")
        key_ids = self.key_ids
        if len(key_ids) != len(set(key_ids)):
            raise ContactMapError("contact map contains duplicate key IDs")
        expected = canonical_hash(
            _contact_revision_payload(
                layout_id=self.layout_id,
                layout_revision=self.layout_revision,
                anchor_revision=self.anchor_revision,
                coordinate_system=self.coordinate_system,
                samples_per_key=self.samples_per_key,
                keys=self.keys,
            )
        )
        if self.revision != expected:
            raise ContactMapError("contact map revision does not match its contents")

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(key.key_id for key in self.keys)

    def key(self, key_id: str) -> ContactKey:
        try:
            return next(key for key in self.keys if key.key_id == key_id)
        except StopIteration as exc:
            raise KeyError(key_id) from exc

    def validate_revisions(
        self,
        inventory: LayoutInventory,
        anchor_revision: str,
    ) -> None:
        if self.layout_id != inventory.layout_id or self.layout_revision != inventory.revision:
            raise RevisionMismatchError(
                "contact map does not match the active layout inventory"
            )
        if self.anchor_revision != anchor_revision:
            raise RevisionMismatchError(
                "contact map does not match the active anchor reference"
            )
        if self.key_ids != inventory.key_ids:
            raise RevisionMismatchError(
                "contact map key order does not match the active inventory"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CONTACT_MAP_SCHEMA_VERSION,
            "layout_id": self.layout_id,
            "layout_revision": self.layout_revision,
            "anchor_revision": self.anchor_revision,
            "coordinate_system": self.coordinate_system,
            "samples_per_key": self.samples_per_key,
            "created_at": self.created_at,
            "revision": self.revision,
            "keys": {key.key_id: key.to_dict() for key in self.keys},
        }


def build_contact_map(
    inventory: LayoutInventory,
    anchor_revision: str,
    samples_by_key: Mapping[str, Sequence[Sequence[object]]],
    *,
    created_at: str | None = None,
) -> KeyboardContactMap:
    if not anchor_revision:
        raise ContactMapError("anchor_revision cannot be empty")
    if set(samples_by_key) != set(inventory.key_ids):
        missing = sorted(set(inventory.key_ids) - set(samples_by_key))
        extra = sorted(set(samples_by_key) - set(inventory.key_ids))
        raise ContactMapError(
            f"contact samples do not match inventory; missing={missing}, extra={extra}"
        )

    keys: list[ContactKey] = []
    for layout_key in inventory.keys:
        raw_samples = samples_by_key[layout_key.key_id]
        samples = tuple(
            _point(value, field=f"{layout_key.key_id}.samples") for value in raw_samples
        )
        if len(samples) != SAMPLES_PER_KEY:
            raise ContactMapError(
                f"{layout_key.key_id} requires exactly {SAMPLES_PER_KEY} samples"
            )
        keys.append(
            ContactKey(
                key_id=layout_key.key_id,
                label=layout_key.label,
                browser_code=layout_key.browser_code,
                model_key_id=layout_key.model_key_id,
                is_wide=layout_key.is_wide,
                samples=samples,
                center=median_point(samples),
            )
        )
    revision_payload = _contact_revision_payload(
        layout_id=inventory.layout_id,
        layout_revision=inventory.revision,
        anchor_revision=anchor_revision,
        coordinate_system=COORDINATE_SYSTEM,
        samples_per_key=SAMPLES_PER_KEY,
        keys=keys,
    )
    return KeyboardContactMap(
        layout_id=inventory.layout_id,
        layout_revision=inventory.revision,
        anchor_revision=anchor_revision,
        keys=tuple(keys),
        created_at=created_at or _now_iso(),
        revision=canonical_hash(revision_payload),
    )


def contact_map_from_dict(payload: Mapping[str, object]) -> KeyboardContactMap:
    if payload.get("schema_version") != CONTACT_MAP_SCHEMA_VERSION:
        raise ContactMapError(f"schema_version must be {CONTACT_MAP_SCHEMA_VERSION}")
    metadata_names = (
        "layout_id",
        "layout_revision",
        "anchor_revision",
        "coordinate_system",
        "created_at",
        "revision",
    )
    metadata: dict[str, str] = {}
    for name in metadata_names:
        value = payload.get(name)
        if not isinstance(value, str) or not value:
            raise ContactMapError(f"contact map {name} is missing or invalid")
        metadata[name] = value
    samples_per_key = payload.get("samples_per_key")
    if isinstance(samples_per_key, bool) or not isinstance(samples_per_key, int):
        raise ContactMapError("samples_per_key must be an integer")
    if samples_per_key != SAMPLES_PER_KEY:
        raise ContactMapError(f"samples_per_key must be {SAMPLES_PER_KEY}")
    raw_keys = payload.get("keys")
    if not isinstance(raw_keys, Mapping) or not raw_keys:
        raise ContactMapError("contact map keys must be a non-empty object")

    keys: list[ContactKey] = []
    for key_id, raw in raw_keys.items():
        if not isinstance(key_id, str) or not key_id or not isinstance(raw, Mapping):
            raise ContactMapError("contact map key entries must be objects")
        raw_samples = raw.get("samples")
        if not isinstance(raw_samples, Sequence) or isinstance(raw_samples, (str, bytes)):
            raise ContactMapError(f"{key_id}.samples must be a list")
        samples = tuple(
            _point(sample, field=f"{key_id}.samples") for sample in raw_samples
        )
        center = _point(raw.get("center"), field=f"{key_id}.center")
        label = raw.get("label", key_id)
        browser_code = raw.get("browser_code", "")
        model_key_id = raw.get("model_key_id", key_id)
        is_wide = raw.get("is_wide", False)
        if not all(isinstance(value, str) for value in (label, browser_code, model_key_id)):
            raise ContactMapError(f"{key_id} string metadata is invalid")
        if not isinstance(is_wide, bool):
            raise ContactMapError(f"{key_id}.is_wide must be boolean")
        keys.append(
            ContactKey(
                key_id,
                label,
                browser_code,
                model_key_id,
                is_wide,
                samples,
                center,
            )
        )

    return KeyboardContactMap(
        layout_id=metadata["layout_id"],
        layout_revision=metadata["layout_revision"],
        anchor_revision=metadata["anchor_revision"],
        keys=tuple(keys),
        created_at=metadata["created_at"],
        revision=metadata["revision"],
        samples_per_key=samples_per_key,
        coordinate_system=metadata["coordinate_system"],
    )


@dataclass(frozen=True, slots=True)
class CalibrationDraft:
    layout_id: str
    layout_revision: str
    anchor_revision: str
    samples_by_key: dict[str, tuple[Point, ...]]
    created_at: str
    updated_at: str
    samples_per_key: int = SAMPLES_PER_KEY

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (
                self.layout_id,
                self.layout_revision,
                self.anchor_revision,
                self.created_at,
                self.updated_at,
            )
        ):
            raise ContactMapError("calibration draft metadata contains an empty value")
        if self.samples_per_key != SAMPLES_PER_KEY:
            raise ContactMapError(f"samples_per_key must be {SAMPLES_PER_KEY}")
        normalized: dict[str, tuple[Point, ...]] = {}
        for key_id, raw_samples in self.samples_by_key.items():
            if not isinstance(key_id, str) or not key_id:
                raise ContactMapError("draft key IDs must be non-empty strings")
            samples = tuple(_point(value, field=f"{key_id}.samples") for value in raw_samples)
            if len(samples) > self.samples_per_key:
                raise ContactMapError(f"{key_id} draft has too many samples")
            normalized[key_id] = samples
        object.__setattr__(self, "samples_by_key", normalized)

    @property
    def complete(self) -> bool:
        return bool(self.samples_by_key) and all(
            len(samples) == self.samples_per_key
            for samples in self.samples_by_key.values()
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": DRAFT_SCHEMA_VERSION,
            "status": "complete" if self.complete else "collecting",
            "layout_id": self.layout_id,
            "layout_revision": self.layout_revision,
            "anchor_revision": self.anchor_revision,
            "coordinate_system": COORDINATE_SYSTEM,
            "samples_per_key": self.samples_per_key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "samples": {
                key_id: [list(point) for point in samples]
                for key_id, samples in self.samples_by_key.items()
            },
        }


def new_calibration_draft(
    inventory: LayoutInventory,
    anchor_revision: str,
    *,
    now: str | None = None,
) -> CalibrationDraft:
    if not anchor_revision:
        raise ContactMapError("anchor_revision cannot be empty")
    timestamp = now or _now_iso()
    return CalibrationDraft(
        layout_id=inventory.layout_id,
        layout_revision=inventory.revision,
        anchor_revision=anchor_revision,
        samples_by_key={key_id: () for key_id in inventory.key_ids},
        created_at=timestamp,
        updated_at=timestamp,
    )


def calibration_draft_from_dict(
    payload: Mapping[str, object],
    inventory: LayoutInventory,
    anchor_revision: str,
) -> CalibrationDraft:
    if payload.get("schema_version") != DRAFT_SCHEMA_VERSION:
        raise ContactMapError(f"schema_version must be {DRAFT_SCHEMA_VERSION}")
    if payload.get("layout_id") != inventory.layout_id or payload.get("layout_revision") != inventory.revision:
        raise RevisionMismatchError(
            "calibration draft does not match the active layout inventory"
        )
    if payload.get("anchor_revision") != anchor_revision:
        raise RevisionMismatchError(
            "calibration draft does not match the active anchor reference"
        )
    if payload.get("coordinate_system", COORDINATE_SYSTEM) != COORDINATE_SYSTEM:
        raise ContactMapError(f"coordinate_system must be {COORDINATE_SYSTEM}")
    if payload.get("samples_per_key") != SAMPLES_PER_KEY:
        raise ContactMapError(f"samples_per_key must be {SAMPLES_PER_KEY}")
    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, Mapping) or set(raw_samples) != set(inventory.key_ids):
        raise ContactMapError("calibration draft samples do not match the inventory")
    samples_by_key: dict[str, tuple[Point, ...]] = {}
    for key_id in inventory.key_ids:
        values = raw_samples[key_id]
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ContactMapError(f"{key_id} draft samples must be a list")
        samples_by_key[key_id] = tuple(
            _point(value, field=f"{key_id}.samples") for value in values
        )
    created_at = payload.get("created_at")
    updated_at = payload.get("updated_at")
    if not isinstance(created_at, str) or not isinstance(updated_at, str):
        raise ContactMapError("calibration draft timestamps are required")
    return CalibrationDraft(
        inventory.layout_id,
        inventory.revision,
        anchor_revision,
        samples_by_key,
        created_at,
        updated_at,
    )


def updated_draft(
    draft: CalibrationDraft,
    samples_by_key: Mapping[str, Sequence[Sequence[object]]],
    *,
    now: str | None = None,
) -> CalibrationDraft:
    if set(samples_by_key) != set(draft.samples_by_key):
        raise ContactMapError("updated draft keys do not match the original draft")
    normalized = {
        key_id: tuple(_point(value, field=f"{key_id}.samples") for value in samples_by_key[key_id])
        for key_id in draft.samples_by_key
    }
    return CalibrationDraft(
        layout_id=draft.layout_id,
        layout_revision=draft.layout_revision,
        anchor_revision=draft.anchor_revision,
        samples_by_key=normalized,
        created_at=draft.created_at,
        updated_at=now or _now_iso(),
        samples_per_key=draft.samples_per_key,
    )


def load_contact_map(
    path: Path,
    *,
    inventory: LayoutInventory | None = None,
    anchor_revision: str | None = None,
) -> KeyboardContactMap:
    if (inventory is None) != (anchor_revision is None):
        raise ContactMapError("inventory and anchor_revision must be supplied together")
    payload = read_json_object(
        Path(path),
        artifact_name="contact map",
        error_factory=ContactMapError,
    )
    contact_map = contact_map_from_dict(payload)
    if inventory is not None and anchor_revision is not None:
        contact_map.validate_revisions(inventory, anchor_revision)
    return contact_map


def save_contact_map(path: Path, contact_map: KeyboardContactMap) -> None:
    validated = contact_map_from_dict(contact_map.to_dict())
    atomic_write_json(Path(path), validated.to_dict())


def load_calibration_draft(
    path: Path,
    inventory: LayoutInventory,
    anchor_revision: str,
) -> CalibrationDraft:
    payload = read_json_object(
        Path(path),
        artifact_name="calibration draft",
        error_factory=ContactMapError,
    )
    return calibration_draft_from_dict(payload, inventory, anchor_revision)


def save_calibration_draft(path: Path, draft: CalibrationDraft) -> None:
    atomic_write_json(Path(path), draft.to_dict())


@dataclass(frozen=True, slots=True)
class KeyCandidate:
    key_id: str
    label: str
    distance: float
    weight: float
    nearest_sample_index: int

    def to_dict(self) -> dict[str, object]:
        return {
            "key_id": self.key_id,
            "label": self.label,
            "distance": self.distance,
            "weight": self.weight,
            "nearest_sample_index": self.nearest_sample_index,
        }


def nearest_sample_candidates(
    contact_map: KeyboardContactMap,
    point: Sequence[object],
    *,
    limit: int = 3,
    distance_scale: float = 1.0,
    max_distance: float | None = None,
) -> tuple[KeyCandidate, ...]:
    target = _point(point, field="point")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ContactMapError("candidate limit must be a positive integer")
    if not math.isfinite(distance_scale) or distance_scale <= 0:
        raise ContactMapError("distance_scale must be a positive finite number")
    if max_distance is not None and (not math.isfinite(max_distance) or max_distance < 0):
        raise ContactMapError("max_distance must be a non-negative finite number")

    candidates: list[KeyCandidate] = []
    for key in contact_map.keys:
        distances = tuple(math.dist(target, sample) for sample in key.samples)
        nearest_index = min(range(len(distances)), key=distances.__getitem__)
        distance = float(distances[nearest_index])
        if max_distance is not None and distance > max_distance:
            continue
        weight = math.exp(-0.5 * (distance / distance_scale) ** 2)
        candidates.append(
            KeyCandidate(key.key_id, key.label, distance, weight, nearest_index)
        )
    candidates.sort(key=lambda candidate: (candidate.distance, candidate.key_id))
    return tuple(candidates[:limit])


__all__ = [
    "CONTACT_MAP_SCHEMA_VERSION",
    "COORDINATE_SYSTEM",
    "DRAFT_SCHEMA_VERSION",
    "INVENTORY_SCHEMA_VERSION",
    "SAMPLES_PER_KEY",
    "CalibrationDraft",
    "ContactKey",
    "ContactMapError",
    "KeyCandidate",
    "KeyboardContactMap",
    "LayoutInventory",
    "LayoutKey",
    "RevisionMismatchError",
    "build_contact_map",
    "build_layout_inventory",
    "calibration_draft_from_dict",
    "contact_map_from_dict",
    "inventory_revision",
    "load_calibration_draft",
    "load_contact_map",
    "median_point",
    "nearest_sample_candidates",
    "new_calibration_draft",
    "save_calibration_draft",
    "save_contact_map",
    "updated_draft",
]
