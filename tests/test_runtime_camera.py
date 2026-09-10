from dataclasses import replace
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from deskvision.calibration.control_plane import KeyboardSetupController, SetupWorkspace
from deskvision.core.config import CameraConfig, load_config
from deskvision.runtime import DeskVisionRuntime, build_runtime
from deskvision.runtime_camera import RuntimeCameraSession
from deskvision.web.settings import RuntimeSettingsController
from tests.test_runtime import _Source, _NoHands, _NoMarkers, ROOT


pytestmark = pytest.mark.integration


class SelectableSource(_Source):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.last_error = None

    def configure(self, config):
        assert not self.is_open
        self.config = config

    def open(self):
        if self.config.device_index == 9:
            self.last_error = "camera 9 unavailable"
            raise RuntimeError(self.last_error)
        self.last_error = None
        super().open()


def make_session(tmp_path, index=0):
    config = load_config(ROOT / "configs/windows.yaml", include_local_override=False)
    config = replace(config, camera=replace(config.camera, device_index=index), artifacts=replace(
        config.artifacts, model_glb=tmp_path / "keyboard.glb", model_manifest=tmp_path / "manifest.json"
    ))
    base = tmp_path / "windows.yaml"
    base.write_text("camera:\n  backend: msmf\n", encoding="utf-8")
    runtime = build_runtime(config, source=SelectableSource(config.camera),
                            hand_tracker=_NoHands(), marker_detector=_NoMarkers())
    settings = RuntimeSettingsController(base, config, "127.0.0.1", 9000, 9000, False)
    setup = KeyboardSetupController(active_artifacts=config.artifacts, camera_config=config.camera,
                                    frames=runtime.frames, states=runtime.states,
                                    workspace=SetupWorkspace(tmp_path / "setup"))
    return RuntimeCameraSession(runtime, settings, setup)


def wait_bundle(runtime):
    deadline = time.monotonic() + 2
    while runtime.states.bundles.latest() is None and time.monotonic() < deadline:
        time.sleep(.005)
    assert runtime.states.bundles.latest() is not None


def test_missing_camera_keeps_runtime_recoverable_without_closing_model(tmp_path):
    session = make_session(tmp_path, index=9)
    runtime = session.runtime
    try:
        runtime.start(allow_camera_failure=True)
        assert runtime.status == "waiting_for_camera"
        assert not session.status()["current"]["running"]
        assert not runtime.pipeline.hand_tracker.closed
        result = session.apply(0, "msmf")
        assert result["current"]["running"]
        wait_bundle(runtime)
        assert runtime.config.camera.device_index == 0
    finally:
        runtime.stop()


def test_failed_switch_clears_old_frames_and_does_not_save_failed_camera(tmp_path):
    session = make_session(tmp_path)
    runtime = session.runtime
    try:
        runtime.start()
        wait_bundle(runtime)
        old_id = runtime.frames.latest().frame_id
        with pytest.raises(RuntimeError, match="camera 9 unavailable"):
            session.apply(9, "msmf")
        assert runtime.frames.latest() is None
        assert runtime.states.latest() is None
        assert runtime.states.bundles.latest() is None
        assert not session.settings.override_config_path.exists()
        assert session.setup.camera_revalidation_path.exists()
        assert runtime.status == "waiting_for_camera"
        session.apply(1, "msmf")
        wait_bundle(runtime)
        assert runtime.frames.latest().frame_id > old_id
        assert load_config(session.settings.base_config_path).camera.device_index == 1
    finally:
        runtime.stop()


def test_incompatible_backend_never_stops_current_pipeline(tmp_path):
    session = make_session(tmp_path)
    runtime = session.runtime
    try:
        runtime.start()
        with pytest.raises(ValueError, match="not supported"):
            session.apply(1, "avfoundation")
        assert runtime.status == "running"
        assert runtime.config.camera.device_index == 0
    finally:
        runtime.stop()


def test_status_does_not_enumerate_or_open_devices(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    monkeypatch.setattr("deskvision.runtime_camera.enumerate_camera_devices",
                        lambda *a, **kw: pytest.fail("GET must be passive"))
    try:
        assert session.status()["devices"] == []
        assert not session.runtime.source.is_open
    finally:
        session.runtime.stop()


def test_save_failure_reports_applied_camera_truthfully(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    runtime = session.runtime
    def fail_save(*args, **kwargs):
        raise OSError("disk unavailable")
    monkeypatch.setattr(RuntimeSettingsController, "save", fail_save)
    try:
        runtime.start()
        with pytest.raises(RuntimeError, match="applied but settings were not saved"):
            session.apply(1, "msmf")
        assert session.status()["current"]["device_index"] == 1
        assert session.status()["current"]["running"]
        assert session.settings.active_config.camera.device_index == 1
        assert session.settings.restart_required
    finally:
        runtime.stop()


def test_view_updates_live_config_and_persists_without_camera_or_calibration_change(tmp_path):
    session = make_session(tmp_path)
    runtime = session.runtime
    original_config = runtime.config.camera
    original_mapper = runtime.pipeline.key_mapper.config
    try:
        # View changes work before acquiring a camera, and never acquire one.
        result = session.apply_view(False, True)
        assert not runtime.source.is_open
        assert runtime.config.camera == original_config
        assert runtime.pipeline.key_mapper.config == original_mapper
        assert not session.setup.camera_revalidation_path.exists()
        assert result["view"] == {"mirror_preview": False, "flip_vertical_preview": True}
        config = load_config(session.settings.base_config_path)
        assert config.debug_ui.mirror_preview is False
        assert config.debug_ui.flip_vertical_preview is True
        client = TestClient(runtime.web_app, base_url="http://127.0.0.1:9000")
        live = client.get("/api/config").json()
        assert live["mirror_preview"] is False
        assert live["flip_vertical_preview"] is True
        assert session.settings.active_config.debug_ui == runtime.config.debug_ui
    finally:
        runtime.stop()


def test_failed_view_persistence_keeps_old_view_and_calibration(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    old_view = session.runtime.config.debug_ui
    def fail_save(*_args, **_kwargs):
        raise OSError("disk unavailable")
    monkeypatch.setattr(RuntimeSettingsController, "save", fail_save)
    try:
        with pytest.raises(OSError, match="disk unavailable"):
            session.apply_view(False, True)
        assert session.runtime.config.debug_ui == old_view
        assert session.settings.active_config.debug_ui == old_view
        assert not session.setup.camera_revalidation_path.exists()
    finally:
        session.runtime.stop()


@pytest.mark.parametrize("mirror,vertical", [(True, False), (False, True), (True, True)])
def test_input_orientation_restarts_consistent_source_and_invalidates_calibration(tmp_path, mirror, vertical):
    session = make_session(tmp_path)
    runtime = session.runtime
    try:
        runtime.start()
        wait_bundle(runtime)
        result = session.apply_orientation(mirror, vertical)
        assert result["current"]["running"]
        assert result["current"]["mirror"] is mirror
        assert result["current"]["flip_vertical"] is vertical
        assert runtime.source.config == runtime.config.camera
        assert runtime.pipeline.key_mapper.config.source_coordinates_mirrored == (mirror ^ vertical)
        assert session.setup.camera_revalidation_path.exists()
        saved = load_config(session.settings.base_config_path)
        assert saved.camera.mirror is mirror
        assert saved.camera.flip_vertical is vertical
    finally:
        runtime.stop()


def test_failed_input_switch_restores_actual_calibration_identity_and_preserves_safety_gate(tmp_path, monkeypatch):
    import json

    session = make_session(tmp_path)
    runtime = session.runtime
    original = DeskVisionRuntime.reconfigure_camera
    def fail_before_configure(*_args, **_kwargs):
        raise RuntimeError("camera shutdown failed before configure")
    try:
        runtime.start()
        wait_bundle(runtime)
        old_camera = runtime.config.camera
        monkeypatch.setattr(DeskVisionRuntime, "reconfigure_camera", fail_before_configure)
        with pytest.raises(RuntimeError, match="shutdown failed"):
            session.apply_orientation(True, True)
        # Still-running old capture and calibration now agree on pixel axes.
        assert runtime.source.is_open
        assert runtime.config.camera == old_camera
        assert session.setup.camera_config == old_camera
        binding = json.loads((session.setup.workspace.root / "camera_binding.json").read_text())
        assert binding["mirror"] is False and binding["flip_vertical"] is False
        assert session.setup.camera_revalidation_path.exists()
        assert session.setup.runtime_camera_revalidation_path.exists()
        assert session.setup.runtime_mapping_block_reason() is not None
        assert not session.settings.override_config_path.exists()

        # A later successful retry is possible; safety does not silently reset.
        monkeypatch.setattr(DeskVisionRuntime, "reconfigure_camera", original)
        session.apply_orientation(True, True)
        assert runtime.config.camera == session.setup.camera_config
        assert runtime.config.camera.mirror and runtime.config.camera.flip_vertical
        assert session.setup.runtime_mapping_block_reason() is not None
    finally:
        runtime.stop()
