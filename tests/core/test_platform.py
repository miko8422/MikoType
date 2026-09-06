import pytest

from deskvision.core.platform import (
    UnsupportedProductionPlatformError,
    is_loopback_host,
    require_windows_runtime,
)


pytestmark = pytest.mark.unit


def test_windows_runtime_gate_accepts_windows_case_insensitively() -> None:
    require_windows_runtime("Windows")
    require_windows_runtime("WINDOWS")


@pytest.mark.parametrize("system", ("Darwin", "Linux", "FreeBSD"))
def test_windows_runtime_gate_rejects_other_hosts(system: str) -> None:
    with pytest.raises(UnsupportedProductionPlatformError, match="Windows-only"):
        require_windows_runtime(system)


@pytest.mark.parametrize("host", ("localhost", "127.0.0.1", "::1"))
def test_v01_service_accepts_loopback_hosts(host: str) -> None:
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ("0.0.0.0", "192.168.1.20", "example.com"))
def test_v01_service_rejects_network_hosts(host: str) -> None:
    assert not is_loopback_host(host)
