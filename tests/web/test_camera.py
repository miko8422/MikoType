"""Camera control checks use callbacks only, never physical camera devices."""

from pathlib import Path
from threading import Event, Thread

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deskvision.core.config import load_config, local_override_path
from deskvision.web.camera import CameraController, create_camera_router
from deskvision.web.settings import RuntimeSettingsController, create_settings_router


pytestmark = pytest.mark.unit


def _camera():
    calls = []
    state = {
        "platform": "macos",
        "available_backends": ["avfoundation", "any"],
        "current": {"device_index": 0, "backend": "avfoundation", "name": "Built-in", "running": False},
        "devices": [],
    }

    def scan():
        calls.append("scan")
        return {**state, "devices": [{"device_index": 1, "backend": "avfoundation", "name": "USB camera"}]}

    def apply(index, backend):
        calls.append((index, backend))
        return {**state, "current": {"device_index": index, "backend": backend, "running": True}}

    controller = CameraController(status=lambda: state, scan=scan, apply=apply)
    app = FastAPI()
    app.include_router(create_camera_router(controller))
    return TestClient(app), controller, calls


def test_status_never_opens_or_scans_camera():
    client, _, calls = _camera()
    for _ in range(3):
        response = client.get("/api/camera")
        assert response.status_code == 200
        assert response.json()["current"]["running"] is False
        assert response.json()["busy"] is False
        assert "Marker reference" in response.json()["calibration_warning"]
    assert calls == []
    assert client.get("/api/camera/scan").status_code == 405


def test_explicit_scan_and_select_delegate_to_runtime():
    client, _, calls = _camera()
    scanned = client.post("/api/camera/scan")
    selected = client.post("/api/camera/select", json={"device_index": 1, "backend": "avfoundation"})
    assert scanned.status_code == 200
    assert scanned.json()["devices"][0]["name"] == "USB camera"
    assert selected.status_code == 200
    assert selected.json()["current"]["device_index"] == 1
    assert selected.json()["current"]["running"] is True
    assert calls == ["scan", (1, "avfoundation")]


@pytest.mark.parametrize("endpoint,names", [
    ("view", ("mirror_preview", "flip_vertical_preview")),
    ("orientation", ("mirror", "flip_vertical")),
])
def test_camera_orientation_endpoint_validates_booleans_and_delegates(endpoint, names):
    client, controller, calls = _camera()
    callback = lambda x, y: calls.append((x, y)) or {"view": dict(zip(names, (x, y)))}
    setattr(controller, f"apply_{endpoint}", callback)
    response = client.post(f"/api/camera/{endpoint}", json=dict(zip(names, (True, False))))
    assert response.status_code == 200
    assert calls == [(True, False)]
    for invalid in [{}, {names[0]: True}, {names[0]: 1, names[1]: False},
                    {names[0]: True, names[1]: "false"},
                    {names[0]: True, names[1]: False, "unexpected": True}]:
        assert client.post(f"/api/camera/{endpoint}", json=invalid).status_code == 422
    assert calls == [(True, False)]


@pytest.mark.parametrize("payload", [
    {}, {"device_index": 0},
    {"device_index": True, "backend": "any"},
    {"device_index": "1", "backend": "any"},
    {"device_index": 1.5, "backend": "any"},
    {"device_index": -1, "backend": "any"},
    {"device_index": 33, "backend": "any"},
    {"device_index": 0, "backend": "other"},
    {"device_index": 0, "backend": "any", "fps": 60},
])
def test_invalid_selection_does_not_touch_camera(payload):
    client, _, calls = _camera()
    assert client.post("/api/camera/select", json=payload).status_code == 422
    assert calls == []


@pytest.mark.parametrize("error,status", [
    (ValueError("wrong platform"), 422),
    (RuntimeError("permission denied; camera stopped"), 409),
    (OSError("device unavailable"), 503),
])
def test_runtime_failure_is_visible_and_does_not_lock_out_retry(error, status):
    client, controller, calls = _camera()
    original = controller.apply

    def fail(index, backend):
        raise error

    controller.apply = fail
    response = client.post("/api/camera/select", json={"device_index": 0, "backend": "any"})
    assert response.status_code == status
    assert str(error) in response.json()["detail"]
    assert client.get("/api/camera").json()["busy"] is False
    controller.apply = original
    assert client.post("/api/camera/select", json={"device_index": 0, "backend": "any"}).status_code == 200
    assert calls == [(0, "any")]


def test_concurrent_camera_action_rejected_but_status_is_readable():
    _, controller, calls = _camera()
    entered, released = Event(), Event()

    def slow_scan():
        entered.set()
        assert released.wait(3)
        return {}

    controller.scan = slow_scan
    thread = Thread(target=controller.scan_devices)
    thread.start()
    try:
        assert entered.wait(3)
        assert controller.get_state()["busy"] is True
        with pytest.raises(RuntimeError, match="尚未完成"):
            controller.select({"device_index": 0, "backend": "any"})
        assert calls == []
    finally:
        released.set()
        thread.join(3)
    assert not thread.is_alive()


def _settings(tmp_path: Path, platform: str):
    backend = "avfoundation" if platform == "macos" else "msmf"
    base = tmp_path / f"{platform}.yaml"
    base.write_text(f"deployment:\n  target_os: {platform}\ncamera:\n  backend: {backend}\n", encoding="utf-8")
    controller = RuntimeSettingsController(
        base_config_path=base, active_config=load_config(base),
        actual_host="127.0.0.1", actual_port=9000, configured_port=9000, auto_selected=False,
    )
    app = FastAPI()
    app.include_router(create_settings_router(controller))
    return TestClient(app), controller, base


@pytest.mark.parametrize("platform,expected", [
    ("macos", ["avfoundation", "any"]), ("windows", ["msmf", "dshow", "any"]),
])
def test_settings_exposes_only_platform_backends(tmp_path, platform, expected):
    client, _, _ = _settings(tmp_path, platform)
    state = client.get("/api/settings").json()
    group = next(item for item in state["groups"] if item["section"] == "camera")
    backend = next(item for item in group["fields"] if item["name"] == "backend")
    assert backend["options"] == expected
    assert state["target_os"] == platform
    assert client.get("/api/service").json()["target_os"] == platform
    page = client.get("/settings").text
    assert 'id="camera-device"' in page
    assert 'id="camera-index"' in page
    assert 'id="camera-apply"' in page


@pytest.mark.parametrize("platform,backend", [("macos", "msmf"), ("windows", "avfoundation")])
def test_mismatched_backend_does_not_write_override(tmp_path, platform, backend):
    client, _, base = _settings(tmp_path, platform)
    response = client.put("/api/settings", json={"values": {"camera": {"backend": backend}}})
    assert response.status_code == 422
    assert not local_override_path(base).exists()


def test_live_camera_acknowledgement_preserves_other_restart_requirements(tmp_path):
    _, controller, _ = _settings(tmp_path, "windows")
    controller.save({"values": {"camera": {"device_index": 1, "backend": "dshow"}}})
    controller.accept_active_camera(1, "dshow")
    assert controller.restart_required is False
    controller.save({"values": {"camera": {"device_index": 2, "fps": 60}}})
    controller.accept_active_camera(2, "dshow")
    assert controller.restart_required is True
    assert controller.active_config.camera.device_index == 2
    assert controller.active_config.camera.fps != 60
