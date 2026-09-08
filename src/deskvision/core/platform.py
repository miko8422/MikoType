"""Explicit platform gates for the portable core and Windows-only VR modules."""

from __future__ import annotations

import ipaddress
import platform


class UnsupportedProductionPlatformError(RuntimeError):
    """The caller attempted to open hardware on the wrong configured host."""


def validate_camera_backend(target_os: str, backend: str) -> None:
    """Reject cross-platform camera configurations before opening hardware."""

    allowed = {
        "windows": {"any", "msmf", "dshow"},
        "macos": {"any", "avfoundation"},
    }
    if target_os not in allowed:
        raise ValueError(f"unsupported core target_os: {target_os!r}")
    if backend.lower() not in allowed[target_os]:
        choices = ", ".join(sorted(allowed[target_os]))
        raise ValueError(
            f"camera backend {backend!r} is not supported for {target_os}; "
            f"choose {choices}"
        )


def require_core_runtime(target_os: str, system: str | None = None) -> None:
    """Allow Mac core testing explicitly; never silently reinterpret Windows config."""

    detected = system or platform.system()
    expected = {"windows": "windows", "macos": "darwin"}.get(target_os)
    if expected is None or detected.casefold() != expected:
        raise UnsupportedProductionPlatformError(
            f"MikoType configuration targets {target_os!r}, but this host reports "
            f"{detected!r}. Use configs/macos.yaml for local Mac camera, mapping "
            "and tracking tests, or configs/windows.yaml on Windows. "
            "SteamVR Home integration remains a separate Windows-only test."
        )


def is_loopback_host(host: str) -> bool:
    """Return whether a configured service host is strictly local."""

    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_windows_runtime(system: str | None = None) -> None:
    """Fail before opening hardware when the production host is not Windows."""

    detected = system or platform.system()
    if detected.casefold() != "windows":
        raise UnsupportedProductionPlatformError(
            "MikoType V0.1 run/setup is Windows-only; this host reports "
            f"{detected!r}. Offline checks and tests may still run here."
        )


__all__ = [
    "UnsupportedProductionPlatformError",
    "is_loopback_host",
    "require_core_runtime",
    "require_windows_runtime",
    "validate_camera_backend",
]
