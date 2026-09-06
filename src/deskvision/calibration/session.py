"""Ordered, resumable five-contact keyboard calibration domain service."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
from pathlib import Path

from .anchor_reference import AnchorReference
from .contact_map import (
    CalibrationDraft,
    ContactMapError,
    KeyboardContactMap,
    LayoutInventory,
    LayoutKey,
    SAMPLES_PER_KEY,
    build_contact_map,
    load_calibration_draft,
    new_calibration_draft,
    save_calibration_draft,
    save_contact_map,
    updated_draft,
)


Point = tuple[float, float]
CALIBRATION_SESSION_SCHEMA_VERSION = "keyboard-contact-calibration-session-0.1"


@dataclass(frozen=True, slots=True)
class CalibrationCapture:
    accepted: bool
    reason: str
    key_id: str | None
    sample_count: int
    samples_required: int
    next_key_id: str | None
    complete: bool
    point_reference: Point | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "key_id": self.key_id,
            "sample_count": self.sample_count,
            "samples_required": self.samples_required,
            "next_key_id": self.next_key_id,
            "complete": self.complete,
            "point_reference": (
                list(self.point_reference) if self.point_reference is not None else None
            ),
        }


class ContactCalibrationSession:
    """Transactional reference-space calibration independent of camera/UI code.

    A camera adapter must transform the selected right-index point into the
    anchor reference coordinate system before calling ``capture_reference_point``.
    This keeps the durable calibration state reusable by WebUI and SteamVR-era
    clients without coupling it to one hand-tracker response format.
    """

    def __init__(
        self,
        inventory: LayoutInventory,
        anchor_reference: AnchorReference,
        draft: CalibrationDraft,
        *,
        draft_path: Path | None = None,
        final_path: Path | None = None,
    ) -> None:
        if draft.layout_id != inventory.layout_id or draft.layout_revision != inventory.revision:
            raise ContactMapError("draft does not match the session layout inventory")
        if draft.anchor_revision != anchor_reference.revision:
            raise ContactMapError("draft does not match the session anchor reference")
        anchor_reference.validate_inventory(inventory)
        self.inventory = inventory
        self.anchor_reference = anchor_reference
        self.draft_path = Path(draft_path) if draft_path is not None else None
        self.final_path = Path(final_path) if final_path is not None else None
        self._draft = draft
        self._samples = {
            key_id: list(samples) for key_id, samples in draft.samples_by_key.items()
        }
        self._paused = False
        self._final_map: KeyboardContactMap | None = None
        if self.complete:
            self._finalize()

    @classmethod
    def resume_or_new(
        cls,
        inventory: LayoutInventory,
        anchor_reference: AnchorReference,
        *,
        draft_path: Path | None = None,
        final_path: Path | None = None,
    ) -> "ContactCalibrationSession":
        if draft_path is not None and Path(draft_path).exists():
            draft = load_calibration_draft(
                Path(draft_path),
                inventory,
                anchor_reference.revision,
            )
        else:
            draft = new_calibration_draft(inventory, anchor_reference.revision)
            if draft_path is not None:
                save_calibration_draft(Path(draft_path), draft)
        return cls(
            inventory,
            anchor_reference,
            draft,
            draft_path=draft_path,
            final_path=final_path,
        )

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def complete(self) -> bool:
        return all(
            len(self._samples[key_id]) == SAMPLES_PER_KEY
            for key_id in self.inventory.key_ids
        )

    @property
    def current_key(self) -> LayoutKey | None:
        for key in self.inventory.keys:
            if len(self._samples[key.key_id]) < SAMPLES_PER_KEY:
                return key
        return None

    @property
    def final_map(self) -> KeyboardContactMap | None:
        return self._final_map

    def pause(self) -> dict[str, object]:
        self._paused = True
        return self.to_dict()

    def resume(self) -> dict[str, object]:
        self._paused = False
        return self.to_dict()

    def progress(self) -> dict[str, object]:
        completed_keys = sum(
            len(self._samples[key_id]) == SAMPLES_PER_KEY
            for key_id in self.inventory.key_ids
        )
        total_samples = sum(len(samples) for samples in self._samples.values())
        required = len(self.inventory.keys) * SAMPLES_PER_KEY
        return {
            "completed_keys": completed_keys,
            "total_keys": len(self.inventory.keys),
            "captured_samples": total_samples,
            "total_samples": required,
            "fraction": total_samples / required,
        }

    def to_dict(self) -> dict[str, object]:
        current = self.current_key
        status = "complete" if self.complete else "paused" if self.paused else "collecting"
        return {
            "schema_version": CALIBRATION_SESSION_SCHEMA_VERSION,
            "status": status,
            "layout_id": self.inventory.layout_id,
            "layout_revision": self.inventory.revision,
            "anchor_revision": self.anchor_reference.revision,
            "samples_per_key": SAMPLES_PER_KEY,
            "progress": self.progress(),
            "current_key": (
                {
                    **current.to_dict(),
                    "sample_count": len(self._samples[current.key_id]),
                    "samples_remaining": SAMPLES_PER_KEY - len(self._samples[current.key_id]),
                    "samples": [list(point) for point in self._samples[current.key_id]],
                }
                if current is not None
                else None
            ),
            "keys": [
                {
                    **key.to_dict(),
                    "sample_count": len(self._samples[key.key_id]),
                    "complete": len(self._samples[key.key_id]) == SAMPLES_PER_KEY,
                    "samples": [list(point) for point in self._samples[key.key_id]],
                }
                for key in self.inventory.keys
            ],
            "contact_map_revision": (
                self._final_map.revision if self._final_map is not None else None
            ),
        }

    def capture_reference_point(
        self,
        point_reference: Sequence[object],
        *,
        key_id: str | None = None,
    ) -> CalibrationCapture:
        current = self.current_key
        blocked = self._blocked_result(current)
        if blocked is not None:
            return blocked
        assert current is not None
        if key_id is not None and key_id != current.key_id:
            return self._result(False, "wrong_key", current)
        if (
            isinstance(point_reference, (str, bytes))
            or len(point_reference) != 2
        ):
            return self._result(False, "invalid_point", current)
        try:
            point = (float(point_reference[0]), float(point_reference[1]))
        except (TypeError, ValueError):
            return self._result(False, "invalid_point", current)
        if not all(math.isfinite(value) for value in point):
            return self._result(False, "invalid_point", current)

        self._samples[current.key_id].append(point)
        try:
            self._persist()
        except Exception:
            self._samples[current.key_id].pop()
            raise
        captured_key = current
        if self.complete:
            self._finalize()
        return self._result(True, "accepted", captured_key, point=point)

    def undo_last(self) -> CalibrationCapture:
        if self.complete:
            return self._result(False, "complete", None)
        if self.paused:
            return self._result(False, "paused", self.current_key)
        for key in reversed(self.inventory.keys):
            if self._samples[key.key_id]:
                removed = self._samples[key.key_id].pop()
                try:
                    self._persist()
                except Exception:
                    self._samples[key.key_id].append(removed)
                    raise
                return self._result(True, "undone", key)
        return self._result(False, "nothing_to_undo", self.current_key)

    def restart_current_key(self) -> CalibrationCapture:
        current = self.current_key
        blocked = self._blocked_result(current)
        if blocked is not None:
            return blocked
        assert current is not None
        previous = list(self._samples[current.key_id])
        self._samples[current.key_id].clear()
        try:
            self._persist()
        except Exception:
            self._samples[current.key_id].extend(previous)
            raise
        return self._result(True, "current_key_restarted", current)

    def _blocked_result(self, current: LayoutKey | None) -> CalibrationCapture | None:
        if self.complete:
            return self._result(False, "complete", None)
        if self.paused:
            return self._result(False, "paused", current)
        return None

    def _result(
        self,
        accepted: bool,
        reason: str,
        key: LayoutKey | None,
        *,
        point: Point | None = None,
    ) -> CalibrationCapture:
        next_key = self.current_key
        return CalibrationCapture(
            accepted=accepted,
            reason=reason,
            key_id=key.key_id if key is not None else None,
            sample_count=len(self._samples[key.key_id]) if key is not None else 0,
            samples_required=SAMPLES_PER_KEY,
            next_key_id=next_key.key_id if next_key is not None else None,
            complete=self.complete,
            point_reference=point,
        )

    def _persist(self) -> None:
        next_draft = updated_draft(self._draft, self._samples)
        if self.draft_path is not None:
            save_calibration_draft(self.draft_path, next_draft)
        self._draft = next_draft

    def _finalize(self) -> None:
        next_map = build_contact_map(
            self.inventory,
            self.anchor_reference.revision,
            self._samples,
            created_at=self._draft.updated_at,
        )
        if self.final_path is not None:
            save_contact_map(self.final_path, next_map)
        self._final_map = next_map


__all__ = [
    "CALIBRATION_SESSION_SCHEMA_VERSION",
    "CalibrationCapture",
    "ContactCalibrationSession",
]
