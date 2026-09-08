"""Camera enumeration contracts, entirely without real camera handles."""

from types import SimpleNamespace
import subprocess

import pytest

from deskvision.core.config import CameraConfig
from deskvision.video import devices


pytestmark = pytest.mark.unit


def test_mac_discovery_uses_inventory_without_opencv_probes(monkeypatch) -> None:
    expected = [{"device_index": 0, "label": "Built-in camera (#0)"}]
    monkeypatch.setattr(devices, "_macos_camera_devices", lambda: expected)
    monkeypatch.setattr(devices.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not probe Mac cameras"))
    assert devices.enumerate_camera_devices(CameraConfig(backend="avfoundation"), system="Darwin") == expected


def test_mac_discovery_reports_inventory_failure_without_guessing_indices(monkeypatch) -> None:
    def unavailable():
        raise OSError("framework unavailable")

    monkeypatch.setattr(devices, "_macos_camera_devices", unavailable)
    with pytest.raises(devices.CameraDiscoveryError, match="framework unavailable"):
        devices.enumerate_camera_devices(CameraConfig(backend="avfoundation"), system="Darwin")


def test_discovery_rejects_backend_for_other_os_before_any_hardware(monkeypatch) -> None:
    monkeypatch.setattr(devices, "_macos_camera_devices", lambda: pytest.fail("must validate first"))
    with pytest.raises(devices.CameraDiscoveryError, match="not supported"):
        devices.enumerate_camera_devices(CameraConfig(backend="msmf"), system="Darwin")


def test_windows_discovery_skips_active_index_and_bounds_probes(monkeypatch) -> None:
    called = []

    def probe(command, **kwargs):
        index = int(command[-2])
        called.append(index)
        assert command[-1] == "CAP_MSMF"
        assert kwargs["timeout"] == 1.5
        assert kwargs["check"] is False
        return SimpleNamespace(returncode=0, stdout="true" if index == 2 else "false")

    monkeypatch.setattr(devices.subprocess, "run", probe)
    result = devices.enumerate_camera_devices(CameraConfig(), system="Windows", active_device_index=1, max_devices=4)
    assert called == [0, 2, 3]
    assert [item["device_index"] for item in result] == [1, 2]
    assert "not probed" in result[0]["label"]


def test_windows_discovery_timeout_or_invalid_response_is_not_selectable(monkeypatch) -> None:
    def probe(command, **kwargs):
        if command[-2] == "0":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout="unexpected output")

    monkeypatch.setattr(devices.subprocess, "run", probe)
    assert devices.enumerate_camera_devices(CameraConfig(), system="Windows", max_devices=2) == []


def test_windows_discovery_retains_high_active_index_without_unbounded_scan(monkeypatch) -> None:
    called = []

    def probe(command, **kwargs):
        called.append(int(command[-2]))
        return SimpleNamespace(returncode=0, stdout="false")

    monkeypatch.setattr(devices.subprocess, "run", probe)
    result = devices.enumerate_camera_devices(CameraConfig(), system="Windows", active_device_index=50, max_devices=2)
    assert called == [0, 1]
    assert [item["device_index"] for item in result] == [50]


def test_windows_discovery_reports_missing_opencv(monkeypatch) -> None:
    monkeypatch.setattr(devices.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=2, stdout=""))
    with pytest.raises(devices.CameraDiscoveryError, match="OpenCV is unavailable"):
        devices.enumerate_camera_devices(CameraConfig(), system="Windows", max_devices=1)


def test_discovery_rejects_unsupported_host() -> None:
    with pytest.raises(devices.CameraDiscoveryError, match="supports Windows/macOS"):
        devices.enumerate_camera_devices(CameraConfig(), system="Linux")


@pytest.mark.parametrize("max_devices", (0, 33, True))
def test_discovery_rejects_unbounded_scans(max_devices) -> None:
    with pytest.raises(ValueError, match="max_devices"):
        devices.enumerate_camera_devices(CameraConfig(), max_devices=max_devices)
