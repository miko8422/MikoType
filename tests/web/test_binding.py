from dataclasses import replace
from pathlib import Path

import pytest

from deskvision.core.config import DeskVisionConfig
from deskvision.web import binding
from deskvision.web.binding import (
    REQUIRED_CONTROL_CAPABILITIES,
    ServicePortInUseError,
    configuration_revision,
    reserve_loopback_endpoint,
    service_identity_mismatch,
)


pytestmark = pytest.mark.unit


class FakeListener:
    def __init__(self) -> None:
        self.closed = False

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


def test_fixed_occupied_port_fails_with_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def occupied(host: str, port: int):
        raise OSError("address already in use")

    monkeypatch.setattr(binding, "_listener", occupied)

    with pytest.raises(ServicePortInUseError, match="--auto-port"):
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
