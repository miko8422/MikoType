"""Stable HTTP/WebSocket routes exposed by production adapters."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RouteContract:
    method: str
    path: str
    purpose: str


ROUTE_CONTRACTS = (
    RouteContract("GET", "/", "browser debug page"),
    RouteContract("GET", "/stream.mjpg", "latest raw MJPEG setup preview"),
    RouteContract("GET", "/snapshot.jpg", "fresh processed-bundle JPEG snapshot"),
    RouteContract("GET", "/api/health", "capture health metrics"),
    RouteContract("GET", "/api/state", "fresh processed-bundle SceneState"),
    RouteContract("WS", "/ws/bundle", "exact FramePacket JPEG + SceneState updates"),
    RouteContract("WS", "/ws/state", "legacy state-only diagnostic updates"),
    RouteContract("GET", "/api/layout", "active user keyboard layout"),
    RouteContract("GET", "/api/model/manifest", "adaptive model manifest"),
    RouteContract("GET", "/api/model/keyboard.glb", "adaptive keyboard GLB"),
    RouteContract("GET", "/setup", "explicit setup-mode control plane"),
)
