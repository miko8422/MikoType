"""Early, exclusive loopback port reservation for the local control plane."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
import socket
from typing import Any, Mapping

from deskvision.core.config import DeskVisionConfig
from deskvision.core.platform import is_loopback_host


SERVICE_SCHEMA_VERSION = "mikotype-service-0.2"
DEFAULT_PORT_SCAN_COUNT = 20
REQUIRED_CONTROL_CAPABILITIES = frozenset(
    {"runtime_inspector", "runtime_settings", "keyboard_setup"}
)
_MAX_SERVICE_RESPONSE_BYTES = 64 * 1024


class ServicePortInUseError(RuntimeError):
    """The requested bounded port range has no available listener."""


def canonical_loopback_host(host: str) -> str:
    """Return a deterministic socket host without leaving loopback."""

    return "127.0.0.1" if host.casefold() == "localhost" else host


def loopback_url(host: str, port: int) -> str:
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{display_host}:{port}"


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value.expanduser().resolve(strict=False))
    return value


def configuration_revision(config: DeskVisionConfig) -> str:
    """Identify the effective requested runtime without hashing user secrets."""

    payload = asdict(config)
    # This acknowledgement gates startup but does not change inference behavior.
    payload["hand_tracking"]["metrics_acknowledged"] = False
    encoded = json.dumps(
        _jsonable(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_path(path: str | Path) -> str:
    return os.path.normcase(
        os.path.normpath(str(Path(path).expanduser().resolve(strict=False)))
    )


def service_identity_mismatch(
    payload: Mapping[str, Any],
    *,
    config_path: Path,
    workspace_path: Path,
    config_revision: str,
) -> str | None:
    """Explain why an existing service cannot safely satisfy this command."""

    raw_capabilities = payload.get("capabilities")
    if not isinstance(raw_capabilities, list) or not all(
        isinstance(item, str) for item in raw_capabilities
    ):
        return "the running service does not advertise control-plane capabilities"
    missing = REQUIRED_CONTROL_CAPABILITIES.difference(raw_capabilities)
    if missing:
        return "the running service lacks: " + ", ".join(sorted(missing))

    raw_config_path = payload.get("config_path")
    if not isinstance(raw_config_path, str) or (
        _canonical_path(raw_config_path) != _canonical_path(config_path)
    ):
        return "the running service uses a different config file"

    raw_workspace_path = payload.get("setup_workspace_path")
    if not isinstance(raw_workspace_path, str) or (
        _canonical_path(raw_workspace_path) != _canonical_path(workspace_path)
    ):
        return "the running service uses a different keyboard setup workspace"

    if payload.get("config_revision") != config_revision:
        return "the running service uses different effective settings"
    return None


@dataclass(slots=True)
class BoundEndpoint:
    host: str
    port: int
    configured_port: int
    listener: socket.socket
    auto_selected: bool = False
    _closed: bool = False

    @property
    def url(self) -> str:
        return loopback_url(self.host, self.port)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.listener.close()

    def __enter__(self) -> "BoundEndpoint":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _listener(host: str, port: int) -> socket.socket:
    bind_host = canonical_loopback_host(host)
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    try:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            listener.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        listener.bind((bind_host, port))
        listener.listen(2048)
        return listener
    except BaseException:
        listener.close()
        raise


def reserve_loopback_endpoint(
    host: str,
    preferred_port: int,
    *,
    allow_fallback: bool = False,
    scan_count: int = DEFAULT_PORT_SCAN_COUNT,
) -> BoundEndpoint:
    """Reserve a listener before camera startup and hand it to Uvicorn."""

    if not is_loopback_host(host):
        raise ValueError("MikoType may reserve only a loopback host")
    if not 1 <= preferred_port <= 65535:
        raise ValueError("service port must be between 1 and 65535")
    if scan_count <= 0:
        raise ValueError("port scan_count must be positive")

    limit = scan_count if allow_fallback else 1
    last_error: OSError | None = None
    for port in range(preferred_port, min(65535, preferred_port + limit - 1) + 1):
        try:
            listener = _listener(host, port)
        except OSError as exc:
            last_error = exc
            continue
        return BoundEndpoint(
            host=canonical_loopback_host(host),
            port=port,
            configured_port=preferred_port,
            listener=listener,
            auto_selected=port != preferred_port,
        )

    if allow_fallback:
        attempted_end = min(65535, preferred_port + limit - 1)
        detail = f"ports {preferred_port}-{attempted_end} are unavailable"
    else:
        detail = (
            f"port {preferred_port} is unavailable and --strict-port disabled "
            "fallback; leave automatic selection enabled or choose --port <PORT>"
        )
    if last_error is not None:
        detail += f" ({last_error})"
    raise ServicePortInUseError(detail)


def probe_mikotype_service(
    host: str,
    port: int,
    *,
    timeout_s: float = 0.25,
) -> Mapping[str, Any] | None:
    """Identify an already-running local MikoType control plane."""

    if not is_loopback_host(host):
        return None
    connection = HTTPConnection(
        canonical_loopback_host(host),
        port,
        timeout=timeout_s,
    )
    try:
        # HTTPConnection talks directly to the exact loopback socket. It neither
        # reads proxy environment variables nor follows redirects, so discovery
        # cannot escape through a global VPN/proxy and changes no system setting.
        connection.request(
            "GET",
            "/api/service",
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-store",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            return None
        raw_payload = response.read(_MAX_SERVICE_RESPONSE_BYTES + 1)
        if len(raw_payload) > _MAX_SERVICE_RESPONSE_BYTES:
            return None
        payload = json.loads(raw_payload.decode("utf-8"))
    except (HTTPException, OSError, UnicodeError, json.JSONDecodeError):
        return None
    finally:
        connection.close()
    if not isinstance(payload, Mapping):
        return None
    if (
        payload.get("service") != "MikoType"
        or payload.get("schema_version") != SERVICE_SCHEMA_VERSION
    ):
        return None
    return payload


__all__ = [
    "BoundEndpoint",
    "DEFAULT_PORT_SCAN_COUNT",
    "REQUIRED_CONTROL_CAPABILITIES",
    "SERVICE_SCHEMA_VERSION",
    "ServicePortInUseError",
    "canonical_loopback_host",
    "configuration_revision",
    "loopback_url",
    "probe_mikotype_service",
    "reserve_loopback_endpoint",
    "service_identity_mismatch",
]
