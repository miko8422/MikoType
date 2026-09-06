"""Atomic-at-file-boundary loading and cross-validation for keyboard artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .anchor_reference import AnchorReference, AnchorReferenceError, load_anchor_reference
from .contact_map import (
    KeyboardContactMap,
    LayoutInventory,
    build_layout_inventory,
    load_contact_map,
)
from .layout_profile import KeyboardLayoutProfile, load_layout_profile


@dataclass(frozen=True, slots=True)
class KeyboardCalibrationArtifacts:
    """One mutually compatible layout, physical anchor, and contact map."""

    layout: KeyboardLayoutProfile
    inventory: LayoutInventory
    anchor_reference: AnchorReference
    contact_map: KeyboardContactMap

    def __post_init__(self) -> None:
        if self.inventory.revision != self.layout.inventory_revision:
            raise ValueError("inventory was not derived from the supplied layout")
        self.anchor_reference.validate_inventory(self.inventory)
        self.contact_map.validate_revisions(
            self.inventory,
            self.anchor_reference.revision,
        )
        planned = {anchor.marker_id: anchor.key_id for anchor in self.layout.anchors}
        for anchor in self.anchor_reference.anchors:
            if planned.get(anchor.marker_id) != anchor.key_id:
                raise AnchorReferenceError(
                    f"marker {anchor.marker_id} does not match the layout anchor plan"
                )

    @property
    def revisions(self) -> dict[str, str]:
        return {
            "layout_content": self.layout.content_hash,
            "layout_inventory": self.inventory.revision,
            "anchor": self.anchor_reference.revision,
            "contact_map": self.contact_map.revision,
        }


def load_keyboard_calibration_artifacts(
    *,
    layout_path: Path,
    anchor_path: Path,
    contact_map_path: Path,
) -> KeyboardCalibrationArtifacts:
    """Load all three artifacts and fail closed on any stale relationship."""
    layout = load_layout_profile(layout_path)
    inventory = build_layout_inventory(layout)
    anchor_reference = load_anchor_reference(anchor_path, inventory=inventory)
    contact_map = load_contact_map(
        contact_map_path,
        inventory=inventory,
        anchor_revision=anchor_reference.revision,
    )
    return KeyboardCalibrationArtifacts(
        layout,
        inventory,
        anchor_reference,
        contact_map,
    )


__all__ = [
    "KeyboardCalibrationArtifacts",
    "load_keyboard_calibration_artifacts",
]
