from dataclasses import replace
import math
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deskvision.core.config import load_config, local_override_path
from deskvision.web.settings import (
    RuntimeSettingsController,
    create_settings_router,
)


pytestmark = pytest.mark.unit


def _client(tmp_path: Path) -> tuple[TestClient, RuntimeSettingsController, Path]:
    base = tmp_path / "windows.yaml"
    base.write_text(
        "app:\n  host: 127.0.0.1\n  port: 8765\n"
        "camera:\n  backend: msmf\n  fps: 60\n",
        encoding="utf-8",
    )
    controller = RuntimeSettingsController(
        base_config_path=base,
        active_config=load_config(base),
        actual_host="127.0.0.1",
        actual_port=8766,
        configured_port=8765,
        auto_selected=True,
        package_version="0.1.test",
        runtime_source=r"M:\\Work\\Project\\VR\\MikoType\\src\\deskvision\\main.py",
        python_executable=r"M:\\Miniconda3\\envs\\mikotype\\python.exe",
    )
    app = FastAPI()
    app.include_router(create_settings_router(controller))
    return TestClient(app), controller, base


def _field(payload: dict[str, object], section: str, name: str) -> dict[str, object]:
    groups = payload["groups"]
    assert isinstance(groups, list)
    group = next(item for item in groups if item["section"] == section)
    return next(item for item in group["fields"] if item["name"] == name)


def test_settings_page_and_service_describe_one_control_plane(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)

    page = client.get("/settings")
    assert page.status_code == 200
    assert 'id="setup-workspace-path"' in page.text
    assert 'id="package-version"' in page.text
    assert 'id="python-executable"' in page.text
    assert 'id="runtime-source"' in page.text
    settings = client.get("/api/settings").json()
    service = client.get("/api/service").json()

    assert _field(settings, "camera", "fps")["value"] == 60
    assert "remote_inference" in settings["protected_sections"]
    assert service["service"] == "MikoType"
    assert service["package_version"] == "0.1.test"
    assert service["runtime_source"].endswith(r"deskvision\\main.py")
    assert service["python_executable"].endswith("python.exe")
    assert service["url"] == "http://127.0.0.1:8766"
    assert service["configured_port"] == 8765
    assert service["auto_selected"] is True
    assert "keyboard_setup" in service["capabilities"]
    assert service["setup_workspace_path"].endswith("data/keyboards/.setup")
    assert len(service["config_revision"]) == 64
    assert "token" not in str(service).lower()


def test_every_numeric_default_satisfies_its_browser_constraints(
    tmp_path: Path,
) -> None:
    client, _, _ = _client(tmp_path)
    groups = client.get("/api/settings").json()["groups"]

    for group in groups:
        for field in group["fields"]:
            if field["type"] not in {"integer", "number"}:
                continue
            value = field["value"]
            minimum = field.get("min")
            maximum = field.get("max")
            step = field.get("step")
            if minimum is not None:
                assert value >= minimum, field["name"]
            if maximum is not None:
                assert value <= maximum, field["name"]
            if step is not None:
                base = minimum if minimum is not None else 0
                steps = (value - base) / step
                assert math.isclose(steps, round(steps), abs_tol=1e-9), field[
                    "name"
                ]


def test_settings_save_validates_and_persists_local_override(tmp_path: Path) -> None:
    client, controller, base = _client(tmp_path)

    response = client.put(
        "/api/settings",
        json={
            "values": {
                "app": {"port": 8877},
                "camera": {"backend": "dshow", "fps": 30},
                "hand_tracking": {"min_tracking_confidence": 0.65},
            }
        },
    )

    assert response.status_code == 200
    assert response.json()["restart_required"] is True
    assert controller.restart_required is True
    assert local_override_path(base).is_file()
    persisted = load_config(base)
    assert persisted.app.port == 8877
    assert persisted.camera.backend == "dshow"
    assert persisted.camera.fps == 30
    assert persisted.hand_tracking.min_tracking_confidence == 0.65


def test_settings_reject_protected_or_invalid_values_without_writing(
    tmp_path: Path,
) -> None:
    client, _, base = _client(tmp_path)

    protected = client.put(
        "/api/settings",
        json={"values": {"remote_inference": {"enabled": True}}},
    )
    invalid = client.put(
        "/api/settings",
        json={"values": {"camera": {"fps": 0}}},
    )

    assert protected.status_code == 422
    assert invalid.status_code == 422
    assert not local_override_path(base).exists()


def test_settings_reset_removes_override_and_reports_restart(tmp_path: Path) -> None:
    client, _, base = _client(tmp_path)
    assert client.put(
        "/api/settings",
        json={"values": {"camera": {"fps": 30}}},
    ).status_code == 200

    response = client.post("/api/settings/reset")

    assert response.status_code == 200
    assert response.json()["restart_required"] is False
    assert not local_override_path(base).exists()


def test_settings_reset_preserves_protected_manual_override(tmp_path: Path) -> None:
    client, _, base = _client(tmp_path)
    override = local_override_path(base)
    override.write_text(
        "camera:\n  fps: 30\n  rotate_degrees: 180\n"
        "remote_inference:\n  enabled: false\n",
        encoding="utf-8",
    )

    response = client.post("/api/settings/reset")

    assert response.status_code == 200
    text = override.read_text(encoding="utf-8")
    assert "rotate_degrees: 180" in text
    assert "remote_inference:" in text
    assert "fps:" not in text


def test_acknowledgement_does_not_create_a_false_restart_requirement(
    tmp_path: Path,
) -> None:
    base = tmp_path / "windows.yaml"
    base.write_text("camera:\n  fps: 60\n", encoding="utf-8")
    configured = load_config(base)
    active = replace(
        configured,
        hand_tracking=replace(
            configured.hand_tracking,
            metrics_acknowledged=True,
        ),
    )
    controller = RuntimeSettingsController(
        base_config_path=base,
        active_config=active,
        actual_host="127.0.0.1",
        actual_port=8765,
        configured_port=8765,
        auto_selected=False,
    )

    saved = controller.save({"values": {"camera": {"fps": 60}}})
    reset = controller.reset()

    assert saved["restart_required"] is False
    assert reset["restart_required"] is False
