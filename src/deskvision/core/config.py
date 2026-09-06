"""Typed production configuration and YAML loading."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, TypeVar


@dataclass(frozen=True, slots=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("app host must be non-empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("app port must be between 1 and 65535")


@dataclass(frozen=True, slots=True)
class CameraConfig:
    source_id: str = "mac_main"
    backend: str = "avfoundation"
    device_index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30
    rotate_degrees: int = 0
    mirror: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("camera source_id must be a non-empty string")
        if not isinstance(self.backend, str):
            raise TypeError("camera backend must be a string")
        if self.backend.lower() not in {"avfoundation", "any"}:
            raise ValueError("camera backend must be 'avfoundation' or 'any'")
        if self.device_index < 0:
            raise ValueError("camera device_index must be non-negative")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("camera width and height must be positive")
        if self.fps <= 0:
            raise ValueError("camera fps must be positive")
        if self.rotate_degrees not in {0, 90, 180, 270}:
            raise ValueError("camera rotate_degrees must be 0, 90, 180, or 270")


@dataclass(frozen=True, slots=True)
class StreamConfig:
    preview_type: str = "mjpeg"
    jpeg_quality: int = 80
    max_preview_fps: int = 30

    def __post_init__(self) -> None:
        if self.preview_type not in {"mjpeg", "snapshot"}:
            raise ValueError("preview_type must be mjpeg or snapshot")
        if not 1 <= self.jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be between 1 and 100")
        if self.max_preview_fps <= 0:
            raise ValueError("max_preview_fps must be positive")


@dataclass(frozen=True, slots=True)
class HandTrackingConfig:
    enabled: bool = True
    model_path: Path | None = None
    num_hands: int = 2
    min_detection_confidence: float = 0.5
    min_presence_confidence: float = 0.5
    min_tracking_confidence: float = 0.5
    metrics_acknowledged: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("hand_tracking.enabled must be boolean")
        if isinstance(self.num_hands, bool) or self.num_hands <= 0:
            raise ValueError("hand_tracking.num_hands must be a positive integer")
        for name in (
            "min_detection_confidence",
            "min_presence_confidence",
            "min_tracking_confidence",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"hand_tracking.{name} must be numeric")
            if not 0 <= value <= 1:
                raise ValueError(f"hand_tracking.{name} must be between 0 and 1")
        if not isinstance(self.metrics_acknowledged, bool):
            raise TypeError("hand_tracking.metrics_acknowledged must be boolean")
        if self.model_path is not None and not isinstance(self.model_path, Path):
            raise TypeError("hand_tracking.model_path must be a Path or None")


@dataclass(frozen=True, slots=True)
class KeyboardTrackingConfig:
    enabled: bool = True
    dictionary: str = "DICT_4X4_50"
    smoothing_alpha: float = 0.38
    max_coast_ms: float = 800.0
    min_pose_confidence: float = 0.25
    min_hand_confidence: float = 0.35
    top_k: int = 3
    direct_weight: float = 0.35
    experimental: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("keyboard_tracking.enabled must be boolean")
        if not isinstance(self.experimental, bool):
            raise TypeError("keyboard_tracking.experimental must be boolean")
        if self.dictionary != "DICT_4X4_50":
            raise ValueError("V0.1 keyboard tracking requires DICT_4X4_50")
        if not 0 < self.smoothing_alpha <= 1:
            raise ValueError("smoothing_alpha must be between 0 and 1")
        if self.max_coast_ms < 0:
            raise ValueError("max_coast_ms cannot be negative")
        for name in (
            "min_pose_confidence",
            "min_hand_confidence",
            "direct_weight",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.top_k <= 0:
            raise ValueError("top_k must be positive")


@dataclass(frozen=True, slots=True)
class InteractionConfig:
    neighbor_glow_enabled: bool = True
    neighbor_glow_scale: float = 0.55
    direct_min_intensity: float = 0.35

    def __post_init__(self) -> None:
        if not isinstance(self.neighbor_glow_enabled, bool):
            raise TypeError("neighbor_glow_enabled must be boolean")
        if self.neighbor_glow_scale <= 0:
            raise ValueError("neighbor_glow_scale must be positive")
        if not 0 <= self.direct_min_intensity <= 1:
            raise ValueError("direct_min_intensity must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ArtifactConfig:
    layout_profile: Path = Path(
        "data/keyboards/kzzi_user_adjustable_82/layout.json"
    )
    anchor_reference: Path = Path(
        "data/keyboards/kzzi_user_adjustable_82/anchor_reference.json"
    )
    contact_map: Path = Path(
        "data/keyboards/kzzi_user_adjustable_82/contact_map.json"
    )
    model_glb: Path = Path(
        "data/keyboards/kzzi_user_adjustable_82/adaptive_keyboard.glb"
    )
    model_manifest: Path = Path(
        "data/keyboards/kzzi_user_adjustable_82/adaptive_keyboard_manifest.json"
    )

    def __post_init__(self) -> None:
        paths = {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }
        if any(not isinstance(path, Path) for path in paths.values()):
            raise TypeError("all artifact locations must be pathlib.Path values")
        resolved: dict[Path, str] = {}
        for name, path in paths.items():
            canonical = path.expanduser().resolve(strict=False)
            previous = resolved.get(canonical)
            if previous is not None:
                raise ValueError(
                    "artifact paths must be unique: "
                    f"{previous} and {name} both resolve to {canonical}"
                )
            resolved[canonical] = name


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    frame_policy: str = "latest"
    perception_enabled: bool = True
    frame_wait_timeout_ms: int = 50
    stop_timeout_s: float = 5.0

    def __post_init__(self) -> None:
        if self.frame_policy != "latest":
            raise ValueError("production pipeline requires the latest frame policy")
        if self.frame_wait_timeout_ms <= 0:
            raise ValueError("frame_wait_timeout_ms must be positive")
        if self.stop_timeout_s <= 0:
            raise ValueError("stop_timeout_s must be positive")


@dataclass(frozen=True, slots=True)
class DebugUIConfig:
    enabled: bool = True
    mirror_preview: bool = True
    websocket_state: bool = True
    expose_model_download: bool = True

    def __post_init__(self) -> None:
        for name in ("enabled", "mirror_preview", "websocket_state", "expose_model_download"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"debug_ui.{name} must be boolean")


@dataclass(frozen=True, slots=True)
class DiagnosticsConfig:
    stale_frame_threshold_ms: int = 500
    metrics_interval_ms: int = 1000

    def __post_init__(self) -> None:
        if (
            isinstance(self.stale_frame_threshold_ms, bool)
            or self.stale_frame_threshold_ms <= 0
        ):
            raise ValueError("stale_frame_threshold_ms must be a positive integer")
        if isinstance(self.metrics_interval_ms, bool) or self.metrics_interval_ms <= 0:
            raise ValueError("metrics_interval_ms must be a positive integer")


@dataclass(frozen=True, slots=True)
class DeskVisionConfig:
    app: AppConfig = field(default_factory=AppConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    hand_tracking: HandTrackingConfig = field(default_factory=HandTrackingConfig)
    keyboard_tracking: KeyboardTrackingConfig = field(
        default_factory=KeyboardTrackingConfig
    )
    interaction: InteractionConfig = field(default_factory=InteractionConfig)
    artifacts: ArtifactConfig = field(default_factory=ArtifactConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    debug_ui: DebugUIConfig = field(default_factory=DebugUIConfig)
    diagnostics: DiagnosticsConfig = field(default_factory=DiagnosticsConfig)


T = TypeVar("T")


def _section(payload: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"config section {name!r} must be an object")
    return dict(value)


def _construct(cls: type[T], payload: Mapping[str, Any], name: str) -> T:
    try:
        return cls(**_section(payload, name))
    except TypeError as exc:
        raise ValueError(f"invalid {name} config: {exc}") from exc


def load_config(path: str | Path) -> DeskVisionConfig:
    """Load one YAML config and resolve artifact paths relative to that file."""

    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load DeskVision config") from exc

    config_path = Path(path).expanduser().resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError("DeskVision config root must be an object")

    artifacts_raw = _section(raw, "artifacts")
    resolved_artifacts: dict[str, Path] = {}
    for name, default_value in ArtifactConfig.__dataclass_fields__.items():
        explicitly_configured = name in artifacts_raw
        value = artifacts_raw.get(name, default_value.default)
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            # Explicit YAML paths are config-relative. Dataclass defaults are
            # project-relative so a minimal configs/*.yaml still finds data/.
            base = config_path.parent if explicitly_configured else config_path.parent.parent
            candidate = base / candidate
        resolved_artifacts[name] = candidate.resolve()

    hand_raw = _section(raw, "hand_tracking")
    model_path = hand_raw.get("model_path")
    if model_path is not None:
        candidate = Path(model_path).expanduser()
        if not candidate.is_absolute():
            candidate = config_path.parent / candidate
        hand_raw["model_path"] = candidate.resolve()

    return DeskVisionConfig(
        app=_construct(AppConfig, raw, "app"),
        camera=_construct(CameraConfig, raw, "camera"),
        stream=_construct(StreamConfig, raw, "stream"),
        hand_tracking=HandTrackingConfig(**hand_raw),
        keyboard_tracking=_construct(
            KeyboardTrackingConfig, raw, "keyboard_tracking"
        ),
        interaction=_construct(InteractionConfig, raw, "interaction"),
        artifacts=ArtifactConfig(**resolved_artifacts),
        pipeline=_construct(PipelineConfig, raw, "pipeline"),
        debug_ui=_construct(DebugUIConfig, raw, "debug_ui"),
        diagnostics=_construct(DiagnosticsConfig, raw, "diagnostics"),
    )


__all__ = [
    "AppConfig",
    "ArtifactConfig",
    "CameraConfig",
    "DebugUIConfig",
    "DeskVisionConfig",
    "DiagnosticsConfig",
    "HandTrackingConfig",
    "InteractionConfig",
    "KeyboardTrackingConfig",
    "PipelineConfig",
    "StreamConfig",
    "load_config",
]
