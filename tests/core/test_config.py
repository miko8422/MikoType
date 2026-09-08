from pathlib import Path

import pytest

from deskvision.core.config import (
    ArtifactConfig,
    CameraConfig,
    DeskVisionConfig,
    RemoteInferenceConfig,
    load_config,
    local_override_path,
)


pytestmark = pytest.mark.unit

REPOSITORY = Path(__file__).resolve().parents[2]


def test_default_config_uses_latest_frame_pipeline() -> None:
    config = DeskVisionConfig()

    assert config.pipeline.frame_policy == "latest"
    assert config.pipeline.perception_enabled is True
    assert config.deployment.target_os == "windows"
    assert config.deployment.topology == "single_host"
    assert config.camera.source_id == "windows_main"
    assert config.camera.backend == "msmf"
    assert config.keyboard_tracking.dictionary == "DICT_4X4_50"
    assert config.debug_ui.enabled is True
    assert config.remote_inference.enabled is False


def test_windows_example_uses_windows_backend_and_valid_artifacts() -> None:
    config = load_config(
        REPOSITORY / "configs" / "windows.yaml",
        include_local_override=False,
    )

    assert config.camera.source_id == "windows_main"
    assert config.camera.backend == "msmf"
    assert config.app.host == "127.0.0.1"
    assert config.deployment.target_os == "windows"
    assert config.deployment.topology == "single_host"
    assert config.remote_inference.enabled is False
    assert config.remote_inference.endpoint is None
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


def test_yaml_loader_applies_gitignored_local_override(tmp_path: Path) -> None:
    path = tmp_path / "windows.yaml"
    path.write_text(
        "app:\n  port: 8765\n"
        "camera:\n  backend: msmf\n  fps: 60\n",
        encoding="utf-8",
    )
    override = local_override_path(path)
    override.write_text(
        "app:\n  port: 8877\n"
        "camera:\n  backend: dshow\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.app.port == 8877
    assert config.camera.backend == "dshow"
    assert config.camera.fps == 60


def test_yaml_loader_can_ignore_local_override(tmp_path: Path) -> None:
    path = tmp_path / "windows.yaml"
    path.write_text("app:\n  port: 8765\n", encoding="utf-8")
    local_override_path(path).write_text("app:\n  port: 8877\n", encoding="utf-8")

    assert load_config(path, include_local_override=False).app.port == 8765


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


@pytest.mark.parametrize("backend", ("any", "msmf", "dshow"))
def test_windows_camera_backends_are_explicit(backend: str) -> None:
    assert CameraConfig(backend=backend).backend == backend


def test_macos_camera_backend_is_explicit() -> None:
    assert CameraConfig(backend="avfoundation").backend == "avfoundation"


def test_unknown_camera_backend_is_rejected() -> None:
    with pytest.raises(ValueError, match="msmf"):
        CameraConfig(backend="invented_backend")


def test_macos_config_isolates_calibration_from_windows_seed() -> None:
    config = load_config(REPOSITORY / "configs/macos.yaml", include_local_override=False)
    windows = load_config(REPOSITORY / "configs/windows.yaml", include_local_override=False)

    assert config.deployment.target_os == "macos"
    assert config.camera.backend == "avfoundation"
    assert config.camera.source_id == "macos_main"
    assert config.remote_inference.enabled is False
    for name in config.artifacts.__dataclass_fields__:
        assert getattr(config.artifacts, name).is_relative_to(REPOSITORY / "data/local/macos")
        assert getattr(config.artifacts, name) != getattr(windows.artifacts, name)


def test_remote_inference_is_disabled_and_requires_an_endpoint() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        RemoteInferenceConfig(enabled=True)


def test_remote_inference_rejects_plaintext_non_loopback_and_url_secrets() -> None:
    with pytest.raises(ValueError, match="wss"):
        RemoteInferenceConfig(endpoint="ws://192.168.1.20/inference")
    with pytest.raises(ValueError, match="credentials"):
        RemoteInferenceConfig(endpoint="wss://token@example.com/inference")
    with pytest.raises(ValueError, match="query"):
        RemoteInferenceConfig(endpoint="wss://example.com/inference?token=secret")


def test_remote_inference_cannot_disable_tls_or_expand_the_frame_limit() -> None:
    with pytest.raises(ValueError, match="remain true"):
        RemoteInferenceConfig(require_tls=False)
    with pytest.raises(ValueError, match="4194304"):
        RemoteInferenceConfig(max_frame_bytes=4 * 1024 * 1024 + 1)
