from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deskvision.core.config import load_config
from deskvision.main import main
from deskvision.state.store import LatestSceneStateStore
from deskvision.video.latest_frame import LatestFrameStore
from deskvision.web.binding import (
    REQUIRED_CONTROL_CAPABILITIES,
    SERVICE_SCHEMA_VERSION,
    ServicePortInUseError,
    configuration_revision,
)


pytestmark = pytest.mark.unit


def test_check_rejects_configuration_that_v01_runtime_cannot_start(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "windows.yaml"
    config.write_text(
        "remote_inference:\n"
        "  enabled: true\n"
        "  endpoint: wss://inference.example.test/ws/experimental/inference\n",
        encoding="utf-8",
    )

    assert main(["check", "--config", str(config)]) == 2
    error = capsys.readouterr().err
    assert "RuntimeBuildError" in error
    assert "not active in V0.1" in error


def test_run_reuses_existing_mikotype_instead_of_starting_second_camera(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = Path("configs/windows.yaml").resolve()
    workspace_path = Path("data/keyboards/.setup").resolve()
    config = load_config(config_path)
    monkeypatch.setattr("deskvision.main.require_windows_runtime", lambda: None)
    monkeypatch.setattr(
        "deskvision.main.probe_mikotype_service",
        lambda host, port: {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "service": "MikoType",
            "url": "http://127.0.0.1:8765",
            "capabilities": sorted(REQUIRED_CONTROL_CAPABILITIES),
            "config_path": str(config_path),
            "setup_workspace_path": str(workspace_path),
            "config_revision": configuration_revision(config),
        },
    )
    monkeypatch.setattr(
        "deskvision.main.build_runtime",
        lambda config: pytest.fail("existing service must prevent camera startup"),
    )

    assert main(["setup"]) == 0
    assert "http://127.0.0.1:8765/setup" in capsys.readouterr().out


def test_run_refuses_to_reuse_mikotype_with_a_different_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = Path("configs/windows.yaml").resolve()
    config = load_config(config_path)
    monkeypatch.setattr("deskvision.main.require_windows_runtime", lambda: None)
    monkeypatch.setattr(
        "deskvision.main.probe_mikotype_service",
        lambda host, port: {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "service": "MikoType",
            "url": "http://127.0.0.1:8765",
            "capabilities": sorted(REQUIRED_CONTROL_CAPABILITIES),
            "config_path": str(config_path),
            "setup_workspace_path": str(tmp_path / "other-workspace"),
            "config_revision": configuration_revision(config),
        },
    )
    monkeypatch.setattr(
        "deskvision.main.reserve_loopback_endpoint",
        lambda *args, **kwargs: pytest.fail("mismatch must fail before reservation"),
    )
    monkeypatch.setattr(
        "deskvision.main.build_runtime",
        lambda config: pytest.fail("mismatch must prevent camera startup"),
    )

    assert main(["run"]) == 2
    assert "different keyboard setup workspace" in capsys.readouterr().err


def test_port_conflict_fails_before_runtime_build(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("deskvision.main.require_windows_runtime", lambda: None)
    monkeypatch.setattr(
        "deskvision.main.probe_mikotype_service", lambda host, port: None
    )
    monkeypatch.setattr(
        "deskvision.main.reserve_loopback_endpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ServicePortInUseError("port occupied; use --auto-port")
        ),
    )
    monkeypatch.setattr(
        "deskvision.main.build_runtime",
        lambda config: pytest.fail("port must be reserved before camera startup"),
    )

    assert main(["run"]) == 2
    assert "--auto-port" in capsys.readouterr().err


def test_control_plane_construction_failure_closes_built_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    source_config = repository / "configs" / "windows.yaml"
    config = load_config(source_config, include_local_override=False)
    events: list[str] = []

    class Endpoint:
        host = "127.0.0.1"
        port = 8765
        configured_port = 8765
        auto_selected = False
        listener = object()
        url = "http://127.0.0.1:8765"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            events.append("port_closed")

    class Runtime:
        frames = LatestFrameStore()
        states = LatestSceneStateStore()
        web_app = FastAPI()
        artifacts = SimpleNamespace(layout=SimpleNamespace(keys=()))

        def stop(self) -> None:
            events.append("runtime_stopped")

    monkeypatch.setattr("deskvision.main.require_windows_runtime", lambda: None)
    monkeypatch.setattr("deskvision.main.load_config", lambda path: config)
    monkeypatch.setattr(
        "deskvision.main.probe_mikotype_service", lambda host, port: None
    )
    monkeypatch.setattr(
        "deskvision.main.reserve_loopback_endpoint",
        lambda host, port, **kwargs: Endpoint(),
    )
    monkeypatch.setattr("deskvision.main.build_runtime", lambda runtime_config: Runtime())
    monkeypatch.setattr(
        "deskvision.main.KeyboardSetupController",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("setup construction failed")),
    )

    assert main(["run", "--config", str(source_config)]) == 2
    assert events == ["runtime_stopped", "port_closed"]


def test_auto_port_mounts_status_settings_and_setup_on_one_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    source_config = repository / "configs" / "windows.yaml"
    config = load_config(source_config, include_local_override=False)
    events: list[str] = []

    class Endpoint:
        host = "127.0.0.1"
        port = 8766
        configured_port = 8765
        auto_selected = True
        listener = object()
        url = "http://127.0.0.1:8766"

        def __enter__(self):
            events.append("port_reserved")
            return self

        def __exit__(self, exc_type, exc, traceback):
            events.append("port_closed")

    class Runtime:
        def __init__(self, runtime_config) -> None:
            self.config = runtime_config
            self.web_app = FastAPI()
            self.frames = LatestFrameStore()
            self.states = LatestSceneStateStore()
            self.artifacts = SimpleNamespace(layout=SimpleNamespace(keys=("key",)))

            @self.web_app.get("/")
            def index():
                return {"ready": True}

        def start(self) -> None:
            events.append("runtime_started")

        def stop(self) -> None:
            events.append("runtime_stopped")

    runtime_holder: dict[str, Runtime] = {}

    def build(runtime_config):
        events.append("runtime_built")
        assert runtime_config.app.port == 8766
        runtime = Runtime(runtime_config)
        runtime_holder["runtime"] = runtime
        return runtime

    class Server:
        def __init__(self, server_config) -> None:
            self.server_config = server_config

        def run(self, *, sockets) -> None:
            events.append("server_ran")
            assert sockets == [Endpoint.listener]
            client = TestClient(
                runtime_holder["runtime"].web_app,
                base_url="http://127.0.0.1:8766",
            )
            assert client.get("/").status_code == 200
            assert client.get("/settings").status_code == 200
            assert client.get("/setup").status_code == 200
            assert client.get("/api/service").json()["port"] == 8766
            assert client.get("/api/setup/layout").status_code == 200

    monkeypatch.setattr("deskvision.main.require_windows_runtime", lambda: None)
    monkeypatch.setattr("deskvision.main.load_config", lambda path: config)
    monkeypatch.setattr(
        "deskvision.main.probe_mikotype_service", lambda host, port: None
    )
    monkeypatch.setattr(
        "deskvision.main.reserve_loopback_endpoint",
        lambda host, port, **kwargs: Endpoint(),
    )
    monkeypatch.setattr("deskvision.main.build_runtime", build)
    monkeypatch.setattr("deskvision.main.uvicorn.Server", Server)

    result = main(
        [
            "run",
            "--config",
            str(source_config),
            "--workspace",
            str(tmp_path / "setup"),
            "--auto-port",
        ]
    )

    assert result == 0
    assert events == [
        "port_reserved",
        "runtime_built",
        "runtime_started",
        "server_ran",
        "runtime_stopped",
        "port_closed",
    ]
