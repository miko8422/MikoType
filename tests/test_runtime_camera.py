from dataclasses import replace
from pathlib import Path
import time

import pytest

from deskvision.calibration.control_plane import KeyboardSetupController, SetupWorkspace
from deskvision.core.config import CameraConfig, load_config
from deskvision.runtime import build_runtime
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
