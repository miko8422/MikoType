from dataclasses import replace
import shutil

import pytest

from deskvision.core.config import ArtifactConfig, DeploymentConfig, load_config
from deskvision.core.local_workspace import initialize_mac_workspace
from tests.test_runtime import ROOT

pytestmark = pytest.mark.unit


def test_mac_initialization_copies_seed_without_changing_windows_or_existing_user_work(tmp_path):
    seed = tmp_path / "data/keyboards/kzzi_user_adjustable_82"
    shutil.copytree(ROOT / "data/keyboards/kzzi_user_adjustable_82", seed)
    target = tmp_path / "data/local/macos/keyboard"
    config = load_config(ROOT / "configs/macos.yaml", include_local_override=False)
    paths = {role: target / getattr(config.artifacts, role).name
             for role in config.artifacts.__dataclass_fields__}
    config = replace(config, artifacts=ArtifactConfig(**paths))
    original = (seed / "layout.json").read_bytes()
    initialize_mac_workspace(config, repository=tmp_path)
    assert (target / "layout.json").read_bytes() == original
    (target / "layout.json").write_text("user work", encoding="utf-8")
    initialize_mac_workspace(config, repository=tmp_path)
    assert (target / "layout.json").read_text() == "user work"
    assert (seed / "layout.json").read_bytes() == original


def test_windows_startup_does_not_create_mac_data(tmp_path):
    config = load_config(ROOT / "configs/windows.yaml", include_local_override=False)
    initialize_mac_workspace(config, repository=tmp_path)
    assert not (tmp_path / "data").exists()
