"""Operating-system gate for the V0.1 production runtime."""

from __future__ import annotations

import ipaddress
import platform


class UnsupportedProductionPlatformError(RuntimeError):
    """The caller attempted to start V0.1 outside its Windows target."""


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
    "require_windows_runtime",
]
