"""Stable HTTP/WebSocket routes exposed by production adapters."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RouteContract:
    method: str
    path: str
    purpose: str


ROUTE_CONTRACTS = (
    RouteContract("GET", "/", "unified runtime inspector"),
    RouteContract("GET", "/settings", "allowlisted restart-safe settings page"),
    RouteContract("GET", "/setup", "read-only setup overview and guided calibration"),
    RouteContract("GET", "/api/setup/status", "available active/staged progress without initializing staging"),
    RouteContract("GET", "/api/camera", "current capture and display orientation"),
    RouteContract("POST", "/api/camera/view", "live display-only horizontal/vertical flips"),
    RouteContract("POST", "/api/camera/orientation", "correct capture pixels and require calibration revalidation"),
    RouteContract("GET", "/steamvr", "Windows SteamVR integration status and logs"),
    RouteContract("GET", "/api/steamvr/status", "bounded bridge/scene/runtime diagnostics"),
    RouteContract("PUT", "/api/steamvr/settings", "session-only manual VR-space alignment"),
    RouteContract("GET", "/api/steamvr/frame.bin", "token-scoped RGBA latest frame and manual pose"),
    RouteContract("POST", "/api/steamvr/events", "token-scoped native bridge diagnostics"),
    RouteContract("GET", "/api/steamvr/diagnostics", "download shareable diagnostics without bridge token"),
    RouteContract("POST", "/api/steamvr/collect-logs", "explicit bounded SteamVR log tails on Windows"),
    RouteContract("GET", "/api/steamvr/driver-assets.zip", "current adaptive keyboard OpenVR render model"),
    RouteContract("GET", "/api/service", "active local service endpoint"),
    RouteContract("GET", "/api/settings", "effective editable settings"),
    RouteContract("PUT", "/api/settings", "persist local settings override"),
    RouteContract("POST", "/api/settings/reset", "remove local settings override"),
    RouteContract("GET", "/stream.mjpg", "latest raw MJPEG setup preview"),
    RouteContract("GET", "/snapshot.jpg", "fresh processed-bundle JPEG snapshot"),
    RouteContract("GET", "/api/health", "capture health metrics"),
    RouteContract("GET", "/api/state", "fresh processed-bundle SceneState"),
    RouteContract("WS", "/ws/bundle", "exact FramePacket JPEG + SceneState updates"),
    RouteContract("WS", "/ws/state", "legacy state-only diagnostic updates"),
    RouteContract("GET", "/api/layout", "active user keyboard layout"),
    RouteContract("GET", "/api/model/manifest", "adaptive model manifest"),
    RouteContract("GET", "/api/model/keyboard.glb", "adaptive keyboard GLB"),
    RouteContract("GET", "/api/setup/layout", "read staged user keyboard layout"),
    RouteContract("PUT", "/api/setup/layout", "stage user keyboard layout"),
    RouteContract("POST", "/api/setup/anchor/start", "start marker registration"),
    RouteContract("POST", "/api/setup/anchor/observe", "observe marker frame"),
    RouteContract("POST", "/api/setup/anchor/finalize", "save anchor reference"),
    RouteContract("GET", "/api/setup/marker/{marker_id}.png", "download marker"),
    RouteContract("POST", "/api/setup/contact/start", "start contact calibration"),
    RouteContract("GET", "/api/setup/contact", "read contact calibration state"),
    RouteContract("POST", "/api/setup/contact/capture", "capture fingertip contact"),
    RouteContract("POST", "/api/setup/contact/undo", "undo fingertip contact"),
    RouteContract("POST", "/api/setup/contact/pause", "pause contact calibration"),
    RouteContract("POST", "/api/setup/contact/reset", "reset contact calibration"),
    RouteContract("POST", "/api/setup/apply", "atomically apply calibrated bundle"),
)
