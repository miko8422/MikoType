"""Create a production keyboard artifact bundle from finalized calibration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from deskvision.calibration._storage import atomic_write_bytes, atomic_write_json
from deskvision.calibration.artifacts import (
    KeyboardCalibrationArtifacts,
    load_keyboard_calibration_artifacts,
)
from deskvision.calibration.anchor_reference import save_anchor_reference
from deskvision.calibration.contact_map import save_contact_map
from deskvision.calibration.layout_profile import save_layout_profile
from deskvision.keyboard.adaptive_model import generate_adaptive_keyboard_model


@dataclass(frozen=True, slots=True)
class KeyboardBundlePaths:
    layout: Path
    anchor: Path
    contact_map: Path
    model: Path
    manifest: Path


@dataclass(frozen=True, slots=True)
class KeyboardBundleResult:
    paths: KeyboardBundlePaths
    artifacts: KeyboardCalibrationArtifacts
    model_revision: str
    model_sha256: str
    key_count: int


def build_keyboard_bundle(
    *,
    source_layout: Path,
    source_anchor: Path,
    source_contact_map: Path,
    destination: KeyboardBundlePaths,
) -> KeyboardBundleResult:
    """Validate, normalize, and atomically write a deployable keyboard bundle."""

    named_paths = {
        "source_layout": source_layout,
        "source_anchor": source_anchor,
        "source_contact_map": source_contact_map,
        "destination_layout": destination.layout,
        "destination_anchor": destination.anchor,
        "destination_contact_map": destination.contact_map,
        "destination_model": destination.model,
        "destination_manifest": destination.manifest,
    }
    resolved: dict[Path, str] = {}
    for name, path in named_paths.items():
        canonical = path.expanduser().resolve(strict=False)
        previous = resolved.get(canonical)
        if previous is not None:
            raise ValueError(
                "keyboard bundle input/output paths must be unique: "
                f"{previous} and {name} both resolve to {canonical}"
            )
        resolved[canonical] = name

    source = load_keyboard_calibration_artifacts(
        layout_path=source_layout,
        anchor_path=source_anchor,
        contact_map_path=source_contact_map,
    )
    persisted_layout = save_layout_profile(destination.layout, source.layout)
    save_anchor_reference(destination.anchor, source.anchor_reference)
    save_contact_map(destination.contact_map, source.contact_map)
    persisted = load_keyboard_calibration_artifacts(
        layout_path=destination.layout,
        anchor_path=destination.anchor,
        contact_map_path=destination.contact_map,
    )
    if persisted.layout.inventory_revision != persisted_layout.inventory_revision:
        raise RuntimeError("persisted layout revision changed during bundle creation")

    generated = generate_adaptive_keyboard_model(
        persisted.layout,
        persisted.contact_map,
    )
    atomic_write_bytes(destination.model, generated.glb)
    atomic_write_json(destination.manifest, generated.manifest)
    model = generated.manifest["model"]
    return KeyboardBundleResult(
        paths=destination,
        artifacts=persisted,
        model_revision=str(generated.manifest["model_revision"]),
        model_sha256=str(model["sha256"]),
        key_count=len(persisted.layout.keys),
    )


__all__ = [
    "KeyboardBundlePaths",
    "KeyboardBundleResult",
    "build_keyboard_bundle",
]
