from __future__ import annotations

import asyncio
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


@pytest.fixture(autouse=True)
def isolate_service_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("deskvision.main.discover_mikotype_services", lambda *args: {})


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
            "url": "http://127.0.0.1:9000",
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

    assert main(["setup", "--strict-port"]) == 0
    assert "http://127.0.0.1:9000/setup" in capsys.readouterr().out


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
            "url": "http://127.0.0.1:9000",
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

    assert main(["run", "--strict-port"]) == 2
    assert "different keyboard setup workspace" in capsys.readouterr().err


def test_default_auto_port_reuses_matching_fallback_service(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = Path("configs/windows.yaml").resolve()
    workspace_path = Path("data/keyboards/.setup").resolve()
    config = load_config(config_path)
    probes: list[int] = []

    def discover(host: str, ports):
        probes.extend(ports)
        return {9700: {
            "schema_version": SERVICE_SCHEMA_VERSION,
            "service": "MikoType",
            "url": "http://127.0.0.1:9700",
            "capabilities": sorted(REQUIRED_CONTROL_CAPABILITIES),
            "config_path": str(config_path),
            "setup_workspace_path": str(workspace_path),
            "config_revision": configuration_revision(config),
        }}

    monkeypatch.setattr("deskvision.main.require_windows_runtime", lambda: None)
    monkeypatch.setattr("deskvision.main.discover_mikotype_services", discover)
    monkeypatch.setattr(
        "deskvision.main.reserve_loopback_endpoint",
        lambda *args, **kwargs: pytest.fail(
            "matching fallback service must be reused before reserving a port"
        ),
    )
    monkeypatch.setattr(
        "deskvision.main.build_runtime",
        lambda config: pytest.fail("fallback reuse must not start a second camera"),
    )

    assert main(["run"]) == 0
    assert probes == list(range(9000, 10001))
    output = capsys.readouterr().out
    assert "discovered fallback port" in output
    assert "OPEN THIS EXACT URL: http://127.0.0.1:9700/" in output


def test_port_conflict_fails_before_runtime_build(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("deskvision.main.require_windows_runtime", lambda: None)
    monkeypatch.setattr(
        "deskvision.main.probe_mikotype_service", lambda host, port: None
    )

    def reject_strict_port(host: str, port: int, **kwargs):
        assert kwargs["allow_fallback"] is False
        raise ServicePortInUseError(
            "port unavailable because --strict-port disabled fallback"
        )

    monkeypatch.setattr(
        "deskvision.main.reserve_loopback_endpoint", reject_strict_port
    )
    monkeypatch.setattr(
        "deskvision.main.build_runtime",
        lambda config: pytest.fail("port must be reserved before camera startup"),
    )

    assert main(["run", "--strict-port"]) == 2
    assert "--strict-port" in capsys.readouterr().err


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
        port = 9000
        configured_port = 9000
        auto_selected = False
        listener = object()
        url = "http://127.0.0.1:9000"

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


@pytest.mark.parametrize("startup_success", [True, False])
def test_default_auto_port_reports_ready_only_after_successful_server_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    startup_success: bool,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    source_config = repository / "configs" / "windows.yaml"
    config = load_config(source_config, include_local_override=False)
    events: list[str] = []

    class Endpoint:
        host = "127.0.0.1"
        port = 9578
        configured_port = 9000
        auto_selected = True
        listener = object()
        url = "http://127.0.0.1:9578"

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
        assert runtime_config.app.port == 9578
        runtime = Runtime(runtime_config)
        runtime_holder["runtime"] = runtime
        return runtime

    class Server:
        def __init__(self, server_config) -> None:
            self.server_config = server_config
            self.started = False

        async def startup(self, sockets=None) -> None:
            assert "MIKOTYPE READY" not in capsys.readouterr().out
            if not startup_success:
                raise SystemExit(3)
            events.append("server_started")
            self.started = True

        def run(self, *, sockets) -> None:
            events.append("server_ran")
            assert sockets == [Endpoint.listener]
            assert self.server_config.workers == 1
            client = TestClient(
                runtime_holder["runtime"].web_app,
                base_url="http://127.0.0.1:9578",
            )
            assert client.get("/").status_code == 200
            assert client.get("/settings").status_code == 200
            assert client.get("/setup").status_code == 200
            assert client.get("/api/service").json()["port"] == 9578
            assert client.get("/api/setup/layout").status_code == 200
            asyncio.run(self.startup(sockets=sockets))

    monkeypatch.setattr("deskvision.main.require_windows_runtime", lambda: None)
    monkeypatch.setattr("deskvision.main.load_config", lambda path: config)
    monkeypatch.setattr(
        "deskvision.main.probe_mikotype_service", lambda host, port: None
    )

    def reserve(host: str, port: int, **kwargs):
        assert (host, port) == ("127.0.0.1", 9000)
        assert kwargs["allow_fallback"] is True
        return Endpoint()

    monkeypatch.setattr("deskvision.main.reserve_loopback_endpoint", reserve)
    monkeypatch.setattr("deskvision.main.build_runtime", build)
    monkeypatch.setattr("deskvision.main.uvicorn.Server", Server)

    result = main(
        [
            "run",
            "--config",
            str(source_config),
            "--workspace",
            str(tmp_path / "setup"),
        ]
    )

    assert result == (0 if startup_success else 2)
    assert events == [
        "port_reserved",
        "runtime_built",
        "runtime_started",
        "server_ran",
        *(["server_started"] if startup_success else []),
        "runtime_stopped",
        "port_closed",
    ]
    captured = capsys.readouterr()
    if startup_success:
        assert "OPEN THIS EXACT URL: http://127.0.0.1:9578/" in captured.out
        assert "OPEN THIS EXACT URL: http://127.0.0.1:9000/" not in captured.out
    else:
        assert "OPEN THIS EXACT URL" not in captured.out
        assert "failed to start (exit 3)" in captured.err
