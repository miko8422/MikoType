from pathlib import Path

import pytest

from deskvision.core.config import ArtifactConfig, DeskVisionConfig, load_config


pytestmark = pytest.mark.unit

REPOSITORY = Path(__file__).resolve().parents[2]


def test_default_config_uses_latest_frame_pipeline() -> None:
    config = DeskVisionConfig()

    assert config.pipeline.frame_policy == "latest"
    assert config.pipeline.perception_enabled is True
    assert config.keyboard_tracking.dictionary == "DICT_4X4_50"
    assert config.debug_ui.enabled is True


def test_windows_example_uses_portable_camera_backend_and_valid_artifacts() -> None:
    config = load_config(REPOSITORY / "configs" / "windows.yaml")

    assert config.camera.source_id == "windows_main"
    assert config.camera.backend == "any"
    assert config.app.host == "127.0.0.1"
    assert config.artifacts.layout_profile.is_file()
    assert config.artifacts.anchor_reference.is_file()
    assert config.artifacts.contact_map.is_file()


def test_yaml_loader_resolves_artifacts_relative_to_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "artifacts:\n  contact_map: ../profile/contact.json\n"
        "hand_tracking:\n  metrics_acknowledged: true\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.artifacts.contact_map == (tmp_path / "../profile/contact.json").resolve()
    assert config.hand_tracking.metrics_acknowledged is True


def test_config_rejects_non_latest_policy(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("pipeline:\n  frame_policy: queue\n", encoding="utf-8")

    with pytest.raises(ValueError, match="latest"):
        load_config(path)


def test_config_rejects_truthy_string_for_boolean(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("debug_ui:\n  enabled: 'false'\n", encoding="utf-8")

    with pytest.raises(ValueError, match="boolean"):
        load_config(path)


def test_artifact_config_rejects_colliding_roles(tmp_path: Path) -> None:
    shared = tmp_path / "shared.json"
    with pytest.raises(ValueError, match="must be unique"):
        ArtifactConfig(
            layout_profile=shared,
            anchor_reference=tmp_path / "anchor.json",
            contact_map=tmp_path / "contact.json",
            model_glb=shared,
            model_manifest=tmp_path / "manifest.json",
        )


def test_minimal_yaml_resolves_default_artifacts_from_project_parent(
    tmp_path: Path,
) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    path = configs / "minimal.yaml"
    path.write_text("{}\n", encoding="utf-8")

    config = load_config(path)

    assert config.artifacts.layout_profile == (
        tmp_path / "data/keyboards/kzzi_user_adjustable_82/layout.json"
    ).resolve()
