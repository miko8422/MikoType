"""Verify scaffold imports and production/demo/test dependency direction."""

import ast
import importlib
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


MODULES = (
    "deskvision",
    "deskvision.main",
    "deskvision.core.config",
    "deskvision.core.lifecycle",
    "deskvision.core.models",
    "deskvision.core.platform",
    "deskvision.video.source",
    "deskvision.video.windows_camera",
    "deskvision.video.latest_frame",
    "deskvision.video.capture",
    "deskvision.video.preprocess",
    "deskvision.video.jpeg_encoder",
    "deskvision.perception.base",
    "deskvision.perception.pipeline",
    "deskvision.perception.noop",
    "deskvision.perception.keyboard_base",
    "deskvision.perception.hand_base",
    "deskvision.perception.mediapipe_hands",
    "deskvision.perception.aruco_keyboard",
    "deskvision.perception.key_candidates",
    "deskvision.perception.worker",
    "deskvision.calibration",
    "deskvision.calibration.layout_profile",
    "deskvision.calibration.anchor_reference",
    "deskvision.calibration.anchor_registration",
    "deskvision.calibration.contact_map",
    "deskvision.calibration.session",
    "deskvision.calibration.artifacts",
    "deskvision.calibration.control_plane",
    "deskvision.keyboard",
    "deskvision.keyboard.adaptive_model",
    "deskvision.keyboard.bundle",
    "deskvision.keyboard.interaction",
    "deskvision.geometry.coordinate_spaces",
    "deskvision.geometry.homography",
    "deskvision.geometry.keyboard_layout",
    "deskvision.state.scene_state",
    "deskvision.state.bundle",
    "deskvision.state.store",
    "deskvision.transport.base",
    "deskvision.transport.local_websocket",
    "deskvision.transport.remote_inference",
    "deskvision.web.api",
    "deskvision.web.app",
    "deskvision.web.setup",
    "deskvision.runtime",
    "deskvision.observability.health",
    "deskvision.observability.metrics",
)


@pytest.mark.parametrize("module_name", MODULES)
def test_scaffold_module_imports(module_name: str) -> None:
    importlib.import_module(module_name)


def test_production_package_does_not_import_demo_or_tests() -> None:
    production_root = Path(__file__).parents[2] / "src" / "deskvision"
    violations: list[str] = []
    for source_path in production_root.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names = [node.module]
            else:
                continue
            for imported_name in imported_names:
                if imported_name.split(".", 1)[0] in {"demo", "tests"}:
                    relative_path = source_path.relative_to(production_root.parent)
                    violations.append(f"{relative_path}:{node.lineno} imports {imported_name}")
    assert not violations, "production dependency boundary violated:\n" + "\n".join(violations)
