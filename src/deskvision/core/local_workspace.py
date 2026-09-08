"""Initialize the opt-in Mac validation workspace without overwriting user data."""

from pathlib import Path
import shutil

from deskvision.core.config import DeskVisionConfig


def initialize_mac_workspace(config: DeskVisionConfig, *, repository: Path) -> None:
    if config.deployment.target_os != "macos":
        return
    destination = repository.resolve() / "data/local/macos/keyboard"
    seed = repository.resolve() / "data/keyboards/kzzi_user_adjustable_82"
    names = {
        "layout_profile": "layout.json",
        "anchor_reference": "anchor_reference.json",
        "contact_map": "contact_map.json",
        "model_glb": "adaptive_keyboard.glb",
        "model_manifest": "adaptive_keyboard_manifest.json",
    }
    # Custom configurations are never seeded implicitly into arbitrary paths.
    if any(getattr(config.artifacts, role).resolve() != destination / name
           for role, name in names.items()):
        return
    primary = tuple(destination / names[role] for role in (
        "layout_profile", "anchor_reference", "contact_map"
    ))
    if any(path.exists() for path in primary):
        return  # Existing/partial user work must be validated, never mixed with seed.
    if not all((seed / name).is_file() for name in names.values()):
        raise RuntimeError("Mac workspace seed is missing; use a complete MikoType checkout")
    destination.mkdir(parents=True, exist_ok=True)
    for name in names.values():
        target = destination / name
        if not target.exists():
            with target.open("xb") as output, (seed / name).open("rb") as source:
                shutil.copyfileobj(source, output)
