from dataclasses import replace
import json
from pathlib import Path

import pytest

from deskvision.core.config import DeskVisionConfig
from deskvision.web import binding
from deskvision.web.binding import (
    REQUIRED_CONTROL_CAPABILITIES,
    SERVICE_SCHEMA_VERSION,
    ServicePortInUseError,
    configuration_revision,
    probe_mikotype_service,
    reserve_loopback_endpoint,
    service_identity_mismatch,
)


pytestmark = pytest.mark.unit


class FakeListener:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeResponse:
    def __init__(
        self,
        status: int,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.read_limits: list[int] = []

    def read(self, limit: int) -> bytes:
        self.read_limits.append(limit)
        return self.body[:limit]

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name, default)


class FakeConnection:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.closed = False

    def request(
        self,
        method: str,
        target: str,
        *,
        headers: dict[str, str],
    ) -> None:
        self.requests.append((method, target, headers))

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


def test_endpoint_holds_listener_until_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    listener = FakeListener()
    monkeypatch.setattr(binding, "_listener", lambda host, port: listener)

    with reserve_loopback_endpoint("127.0.0.1", 8765) as endpoint:
        assert endpoint.port == 8765
        assert endpoint.listener is listener
        assert listener.closed is False

    assert listener.closed is True


def test_strict_occupied_port_fails_with_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def occupied(host: str, port: int):
        raise OSError("address already in use")

    monkeypatch.setattr(binding, "_listener", occupied)

    with pytest.raises(ServicePortInUseError, match="--strict-port"):
        reserve_loopback_endpoint("127.0.0.1", 8765)


def test_auto_port_selects_next_available_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted: list[int] = []
    listener = FakeListener()

    def select(host: str, port: int):
        attempted.append(port)
        if port < 8767:
            raise OSError("address already in use")
        return listener

    monkeypatch.setattr(binding, "_listener", select)

    with reserve_loopback_endpoint(
        "127.0.0.1",
        8765,
        allow_fallback=True,
        scan_count=3,
    ) as endpoint:
        assert endpoint.port == 8767
        assert endpoint.auto_selected is True
        assert endpoint.configured_port == 8765

    assert attempted == [8765, 8766, 8767]
    assert listener.closed is True


def test_endpoint_rejects_non_loopback_and_invalid_scan() -> None:
    with pytest.raises(ValueError, match="loopback"):
        reserve_loopback_endpoint("0.0.0.0", 8765)
    with pytest.raises(ValueError, match="positive"):
        reserve_loopback_endpoint("127.0.0.1", 8765, scan_count=0)


def test_service_probe_uses_exact_direct_loopback_connection_and_ignores_proxy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "service": "MikoType",
        "schema_version": SERVICE_SCHEMA_VERSION,
    }
    response = FakeResponse(200, json.dumps(payload).encode("utf-8"))
    connection = FakeConnection(response)
    connections: list[tuple[str, int, float]] = []

    def connect(host: str, port: int, *, timeout: float):
        connections.append((host, port, timeout))
        return connection

    monkeypatch.setenv("HTTP_PROXY", "http://vpn-proxy.invalid:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://vpn-proxy.invalid:8080")
    monkeypatch.setattr(binding, "HTTPConnection", connect)

    assert probe_mikotype_service("localhost", 8766, timeout_s=0.75) == payload
    assert connections == [("127.0.0.1", 8766, 0.75)]
    assert connection.requests == [
        (
            "GET",
            "/api/service",
            {
                "Accept": "application/json",
                "Cache-Control": "no-store",
                "Connection": "close",
            },
        )
    ]
    assert connection.closed is True


def test_service_probe_rejects_redirect_without_following_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(
        FakeResponse(
            302,
            headers={"Location": "http://vpn-proxy.invalid/not-loopback"},
        )
    )
    connections: list[FakeConnection] = []

    def connect(host: str, port: int, *, timeout: float):
        connections.append(connection)
        return connection

    monkeypatch.setattr(binding, "HTTPConnection", connect)

    assert probe_mikotype_service("127.0.0.1", 8765) is None
    assert connections == [connection]
    assert len(connection.requests) == 1
    assert connection.response.read_limits == []
    assert connection.closed is True


def test_service_probe_rejects_response_larger_than_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(
        200,
        b"x" * (binding._MAX_SERVICE_RESPONSE_BYTES + 1),
    )
    connection = FakeConnection(response)
    monkeypatch.setattr(
        binding,
        "HTTPConnection",
        lambda host, port, *, timeout: connection,
    )

    assert probe_mikotype_service("127.0.0.1", 8765) is None
    assert response.read_limits == [binding._MAX_SERVICE_RESPONSE_BYTES + 1]
    assert connection.closed is True


def test_configuration_revision_ignores_startup_acknowledgement() -> None:
    config = DeskVisionConfig()
    acknowledged = replace(
        config,
        hand_tracking=replace(
            config.hand_tracking,
            metrics_acknowledged=True,
        ),
    )

    assert configuration_revision(acknowledged) == configuration_revision(config)


def test_service_identity_requires_matching_config_workspace_and_capabilities(
    tmp_path: Path,
) -> None:
    config = DeskVisionConfig()
    config_path = tmp_path / "windows.yaml"
    workspace = tmp_path / "setup"
    payload = {
        "capabilities": sorted(REQUIRED_CONTROL_CAPABILITIES),
        "config_path": str(config_path),
        "setup_workspace_path": str(workspace),
        "config_revision": configuration_revision(config),
    }

    assert service_identity_mismatch(
        payload,
        config_path=config_path,
        workspace_path=workspace,
        config_revision=configuration_revision(config),
    ) is None
    assert "workspace" in str(
        service_identity_mismatch(
            payload,
            config_path=config_path,
            workspace_path=tmp_path / "other",
            config_revision=configuration_revision(config),
        )
    )
