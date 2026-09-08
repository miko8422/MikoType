import pytest

from deskvision.core.platform import (
    UnsupportedProductionPlatformError,
    is_loopback_host,
    require_core_runtime,
    require_windows_runtime,
    validate_camera_backend,
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


@pytest.mark.parametrize(("target", "system"), (("windows", "Windows"), ("macos", "Darwin"), ("macos", "DARWIN")))
def test_core_platform_gate_supports_explicit_local_target(target: str, system: str) -> None:
    require_core_runtime(target, system)


@pytest.mark.parametrize(("target", "system"), (("windows", "Darwin"), ("macos", "Windows"), ("macos", "Linux")))
def test_core_platform_never_silently_changes_config(target: str, system: str) -> None:
    with pytest.raises(UnsupportedProductionPlatformError, match="configuration targets"):
        require_core_runtime(target, system)


@pytest.mark.parametrize(("target", "backend"), (("macos", "avfoundation"), ("macos", "any"), ("windows", "msmf"), ("windows", "dshow")))
def test_target_camera_backend_validation_accepts_supported_pairs(target: str, backend: str) -> None:
    validate_camera_backend(target, backend)


@pytest.mark.parametrize(("target", "backend"), (("macos", "msmf"), ("macos", "dshow"), ("windows", "avfoundation")))
def test_target_camera_backend_validation_rejects_cross_platform_pairs(target: str, backend: str) -> None:
    with pytest.raises(ValueError, match="not supported"):
        validate_camera_backend(target, backend)


def test_runtime_gates_real_hardware_before_loading_models(monkeypatch) -> None:
    from deskvision.core.config import DeskVisionConfig
    from deskvision import runtime

    def denied(target):
        assert target == "windows"
        raise UnsupportedProductionPlatformError("synthetic wrong host")

    monkeypatch.setattr(runtime, "require_core_runtime", denied)
    monkeypatch.setattr(runtime, "validate_runtime_config", lambda config: pytest.fail("must gate hardware first"))
    with pytest.raises(UnsupportedProductionPlatformError, match="synthetic wrong host"):
        runtime.build_runtime(DeskVisionConfig())


def test_injected_camera_does_not_require_host_hardware(monkeypatch) -> None:
    from deskvision.core.config import DeskVisionConfig
    from deskvision import runtime

    class ValidationReached(RuntimeError):
        pass

    def validated(config):
        raise ValidationReached("hardware-free validation")

    monkeypatch.setattr(runtime, "require_core_runtime", lambda target: pytest.fail("must not gate injected source"))
    monkeypatch.setattr(runtime, "validate_runtime_config", validated)
    with pytest.raises(ValidationReached):
        runtime.build_runtime(DeskVisionConfig(), source=object())


def test_runtime_rejects_cross_platform_backend_before_loading_models() -> None:
    from deskvision.core.config import CameraConfig, DeskVisionConfig
    from deskvision.runtime import RuntimeBuildError, validate_runtime_config

    with pytest.raises(RuntimeBuildError, match="not supported"):
        validate_runtime_config(DeskVisionConfig(camera=CameraConfig(backend="avfoundation")))
