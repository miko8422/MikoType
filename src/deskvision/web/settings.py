"""Allowlisted, restart-safe settings for the local MikoType control plane."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import math
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from deskvision.core.config import (
    DeskVisionConfig,
    load_config,
    local_override_path,
)
from deskvision.web.app import STATIC_DIR
from deskvision.web.binding import (
    REQUIRED_CONTROL_CAPABILITIES,
    SERVICE_SCHEMA_VERSION,
    configuration_revision,
    loopback_url,
)


SETTINGS_SCHEMA_VERSION = "mikotype-settings-0.1"


@dataclass(frozen=True, slots=True)
class SettingField:
    section: str
    name: str
    label: str
    description: str
    field_type: str
    minimum: float | int | None = None
    maximum: float | int | None = None
    step: float | int | None = None
    options: tuple[str, ...] = ()

    def serialize(self, config: DeskVisionConfig) -> dict[str, object]:
        result: dict[str, object] = {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "type": self.field_type,
            "value": getattr(getattr(config, self.section), self.name),
            "restart_required": True,
        }
        if self.minimum is not None:
            result["min"] = self.minimum
        if self.maximum is not None:
            result["max"] = self.maximum
        if self.step is not None:
            result["step"] = self.step
        if self.options:
            result["options"] = list(self.options)
        return result

    def normalize(self, value: object) -> object:
        if self.field_type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{self.section}.{self.name} must be boolean")
            return value
        if self.field_type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{self.section}.{self.name} must be an integer")
            normalized: object = value
        elif self.field_type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{self.section}.{self.name} must be numeric")
            normalized = float(value)
        elif self.field_type == "select":
            if not isinstance(value, str) or value not in self.options:
                allowed = ", ".join(self.options)
                raise ValueError(
                    f"{self.section}.{self.name} must be one of: {allowed}"
                )
            return value
        else:  # pragma: no cover - field definitions are module constants.
            raise RuntimeError(f"unsupported settings field type {self.field_type!r}")

        if isinstance(normalized, float) and not math.isfinite(normalized):
            raise ValueError(f"{self.section}.{self.name} must be finite")
        if self.minimum is not None and normalized < self.minimum:
            raise ValueError(
                f"{self.section}.{self.name} must be at least {self.minimum}"
            )
        if self.maximum is not None and normalized > self.maximum:
            raise ValueError(
                f"{self.section}.{self.name} must be at most {self.maximum}"
            )
        return normalized


@dataclass(frozen=True, slots=True)
class SettingGroup:
    section: str
    label: str
    description: str
    fields: tuple[SettingField, ...]


def _field(
    section: str,
    name: str,
    label: str,
    description: str,
    field_type: str,
    *,
    minimum: float | int | None = None,
    maximum: float | int | None = None,
    step: float | int | None = None,
    options: tuple[str, ...] = (),
) -> SettingField:
    return SettingField(
        section,
        name,
        label,
        description,
        field_type,
        minimum,
        maximum,
        step,
        options,
    )


SETTING_GROUPS = (
    SettingGroup(
        "app",
        "本地服务",
        "端口和日志级别在下次启动时生效；服务始终限制在本机回环地址。",
        (
            _field("app", "port", "首选端口", "默认 8765。冲突时可用 --auto-port 启动。", "integer", minimum=1, maximum=65535, step=1),
            _field("app", "log_level", "日志级别", "控制终端日志详细程度。", "select", options=("DEBUG", "INFO", "WARNING", "ERROR")),
        ),
    ),
    SettingGroup(
        "camera",
        "摄像头",
        "选择 Windows 摄像头、OpenCV 后端和请求的采集规格。",
        (
            _field("camera", "device_index", "设备编号", "通常内置/首个摄像头为 0。", "integer", minimum=0, maximum=32, step=1),
            _field("camera", "backend", "采集后端", "优先 msmf；异常时依次尝试 dshow、any。", "select", options=("msmf", "dshow", "any")),
            _field("camera", "width", "请求宽度", "摄像头可能选择最接近的支持值。", "integer", minimum=160, maximum=7680, step=1),
            _field("camera", "height", "请求高度", "摄像头可能选择最接近的支持值。", "integer", minimum=120, maximum=4320, step=1),
            _field("camera", "fps", "请求帧率", "实际帧率仍取决于摄像头和后端。", "integer", minimum=1, maximum=240, step=1),
        ),
    ),
    SettingGroup(
        "stream",
        "浏览器预览",
        "只影响调试画面编码，不改变算法收到的原始帧。",
        (
            _field("stream", "jpeg_quality", "JPEG 质量", "较低值减少浏览器传输开销。", "integer", minimum=1, maximum=100, step=1),
            _field("stream", "max_preview_fps", "预览 FPS 上限", "算法仍按最新原始帧运行。", "integer", minimum=1, maximum=120, step=1),
        ),
    ),
    SettingGroup(
        "hand_tracking",
        "MediaPipe 手部追踪",
        "控制手数及检测、存在和连续追踪置信度。",
        (
            _field("hand_tracking", "num_hands", "最大手数", "V0.1 通常使用双手。", "integer", minimum=1, maximum=4, step=1),
            _field("hand_tracking", "min_detection_confidence", "检测阈值", "越高越保守。", "number", minimum=0, maximum=1, step=0.05),
            _field("hand_tracking", "min_presence_confidence", "存在阈值", "控制当前帧手部有效性。", "number", minimum=0, maximum=1, step=0.05),
            _field("hand_tracking", "min_tracking_confidence", "追踪阈值", "控制连续帧追踪稳定性。", "number", minimum=0, maximum=1, step=0.05),
        ),
    ),
    SettingGroup(
        "keyboard_tracking",
        "Marker 与键位推断",
        "调整 ArUco 坐标稳定性和 Contact Map 候选策略。",
        (
            _field("keyboard_tracking", "smoothing_alpha", "平滑系数", "越高越跟手，越低越稳定。", "number", minimum=0.01, maximum=1, step=0.01),
            _field("keyboard_tracking", "max_coast_ms", "Marker 丢失续航 ms", "短暂丢失时沿用最近可信位姿。", "number", minimum=0, maximum=5000, step=10),
            _field("keyboard_tracking", "min_pose_confidence", "键盘位姿阈值", "较低值更容易保持定位。", "number", minimum=0, maximum=1, step=0.05),
            _field("keyboard_tracking", "min_hand_confidence", "指尖阈值", "过滤低可信指尖。", "number", minimum=0, maximum=1, step=0.05),
            _field("keyboard_tracking", "top_k", "候选键数量", "每个指尖最多输出的键位候选。", "integer", minimum=1, maximum=10, step=1),
            _field("keyboard_tracking", "direct_weight", "直接命中权重", "控制空间距离在首选键判断中的权重。", "number", minimum=0, maximum=1, step=0.05),
        ),
    ),
    SettingGroup(
        "interaction",
        "键帽高亮",
        "控制首选键与邻近键的视觉强度。",
        (
            _field("interaction", "neighbor_glow_enabled", "邻键弱光", "关闭后只显示直接候选。", "boolean"),
            _field("interaction", "neighbor_glow_scale", "邻键亮度比例", "相对首选候选的亮度缩放。", "number", minimum=0.01, maximum=2, step=0.01),
            _field("interaction", "direct_min_intensity", "直接高亮最低强度", "限制首选键最低可见度。", "number", minimum=0, maximum=1, step=0.05),
        ),
    ),
    SettingGroup(
        "pipeline",
        "流水线与关闭",
        "调节最新帧工作线程的等待和安全关闭期限。",
        (
            _field("pipeline", "frame_wait_timeout_ms", "帧等待 ms", "工作线程等待下一帧的时间片。", "integer", minimum=1, maximum=1000, step=1),
            _field("pipeline", "stop_timeout_s", "关闭超时 s", "线程安全停止的最长等待时间。", "number", minimum=0.1, maximum=60, step=0.1),
        ),
    ),
    SettingGroup(
        "debug_ui",
        "界面显示",
        "只改变浏览器预览方向，不改变算法坐标。",
        (
            _field("debug_ui", "mirror_preview", "镜像预览", "让画面看起来像镜子。", "boolean"),
        ),
    ),
    SettingGroup(
        "diagnostics",
        "诊断",
        "定义过期帧保护和健康指标刷新间隔。",
        (
            _field("diagnostics", "stale_frame_threshold_ms", "过期帧阈值 ms", "超过后界面和状态都会失效关闭。", "integer", minimum=50, maximum=10000, step=10),
            _field("diagnostics", "metrics_interval_ms", "指标刷新 ms", "浏览器健康状态轮询间隔。", "integer", minimum=100, maximum=10000, step=100),
        ),
    ),
)


FIELD_INDEX = {
    (field.section, field.name): field
    for group in SETTING_GROUPS
    for field in group.fields
}


def _settings_values(config: DeskVisionConfig) -> dict[str, dict[str, object]]:
    values: dict[str, dict[str, object]] = {}
    for group in SETTING_GROUPS:
        section = getattr(config, group.section)
        values[group.section] = {
            field.name: getattr(section, field.name) for field in group.fields
        }
    return values


def _read_override(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - required project dependency.
        raise RuntimeError("PyYAML is required to save MikoType settings") from exc
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise ValueError("local settings file root must be an object")
    return dict(payload)


def _atomic_write_yaml(path: Path, payload: Mapping[str, object]) -> None:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - required project dependency.
        raise RuntimeError("PyYAML is required to save MikoType settings") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        "# Generated by the local MikoType settings UI.\n"
        "# Values are applied only after the service restarts.\n"
        + yaml.safe_dump(dict(payload), allow_unicode=True, sort_keys=True)
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


@dataclass(slots=True)
class RuntimeSettingsController:
    base_config_path: Path
    active_config: DeskVisionConfig
    actual_host: str
    actual_port: int
    configured_port: int
    auto_selected: bool
    setup_workspace_path: Path = Path("data/keyboards/.setup")
    mode: str = "run"
    config_revision: str | None = None
    _configured_config: DeskVisionConfig | None = None
    _restart_required: bool = False
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_config_path = self.base_config_path.expanduser().resolve()
        self.setup_workspace_path = self.setup_workspace_path.expanduser().resolve()
        if self.config_revision is None:
            self.config_revision = configuration_revision(self.active_config)
        self._configured_config = self.active_config

    @property
    def override_config_path(self) -> Path:
        return local_override_path(self.base_config_path)

    @property
    def restart_required(self) -> bool:
        with self._lock:
            return self._restart_required

    def settings_state(self) -> dict[str, object]:
        with self._lock:
            configured = self._configured_config or self.active_config
            return {
                "schema_version": SETTINGS_SCHEMA_VERSION,
                "restart_required": self._restart_required,
                "base_config_path": str(self.base_config_path),
                "override_config_path": str(self.override_config_path),
                "groups": [
                    {
                        "section": group.section,
                        "label": group.label,
                        "description": group.description,
                        "fields": [
                            field.serialize(configured) for field in group.fields
                        ],
                    }
                    for group in SETTING_GROUPS
                ],
                "protected_sections": [
                    "deployment",
                    "artifacts",
                    "remote_inference",
                    "hand_tracking.metrics_acknowledged",
                ],
            }

    def service_state(self) -> dict[str, object]:
        with self._lock:
            url = loopback_url(self.actual_host, self.actual_port)
            return {
                "schema_version": SERVICE_SCHEMA_VERSION,
                "service": "MikoType",
                "mode": self.mode,
                "host": self.actual_host,
                "port": self.actual_port,
                "url": url,
                "configured_port": self.configured_port,
                "auto_selected": self.auto_selected,
                "capabilities": sorted(REQUIRED_CONTROL_CAPABILITIES),
                "setup_url": f"{url}/setup",
                "settings_url": f"{url}/settings",
                "config_path": str(self.base_config_path),
                "setup_workspace_path": str(self.setup_workspace_path),
                "config_revision": self.config_revision,
                "override_config_path": str(self.override_config_path),
                "restart_required": self._restart_required,
            }

    def save(self, payload: Mapping[str, object]) -> dict[str, object]:
        with self._lock:
            return self._save_unlocked(payload)

    def _save_unlocked(self, payload: Mapping[str, object]) -> dict[str, object]:
        values = payload.get("values")
        if not isinstance(values, Mapping):
            raise ValueError("settings payload must contain an object named 'values'")
        configured = self._configured_config or self.active_config
        candidate = configured
        for section_name, raw_section in values.items():
            if not isinstance(section_name, str) or not isinstance(raw_section, Mapping):
                raise ValueError("each settings section must be an object")
            group = next(
                (item for item in SETTING_GROUPS if item.section == section_name),
                None,
            )
            if group is None:
                raise ValueError(f"settings section {section_name!r} is protected")
            changes: dict[str, object] = {}
            for name, value in raw_section.items():
                field = FIELD_INDEX.get((section_name, name))
                if field is None:
                    raise ValueError(
                        f"setting {section_name}.{name} is unknown or protected"
                    )
                changes[name] = field.normalize(value)
            if changes:
                candidate_section = replace(getattr(candidate, section_name), **changes)
                candidate = replace(candidate, **{section_name: candidate_section})

        override = _read_override(self.override_config_path)
        candidate_values = _settings_values(candidate)
        for group in SETTING_GROUPS:
            existing = override.get(group.section)
            section_override = dict(existing) if isinstance(existing, Mapping) else {}
            section_override.update(candidate_values[group.section])
            override[group.section] = section_override
        _atomic_write_yaml(self.override_config_path, override)

        # Re-read the combined configuration before acknowledging persistence.
        persisted = load_config(self.base_config_path)
        self._configured_config = persisted
        self._restart_required = (
            configuration_revision(persisted)
            != configuration_revision(self.active_config)
        )
        return self.settings_state()

    def reset(self) -> dict[str, object]:
        with self._lock:
            return self._reset_unlocked()

    def _reset_unlocked(self) -> dict[str, object]:
        override = _read_override(self.override_config_path)
        for group in SETTING_GROUPS:
            existing = override.get(group.section)
            if not isinstance(existing, Mapping):
                continue
            remaining = dict(existing)
            for field in group.fields:
                remaining.pop(field.name, None)
            if remaining:
                override[group.section] = remaining
            else:
                override.pop(group.section, None)
        if override:
            _atomic_write_yaml(self.override_config_path, override)
        else:
            self.override_config_path.unlink(missing_ok=True)
        configured = load_config(self.base_config_path)
        self._configured_config = configured
        self._restart_required = (
            configuration_revision(configured)
            != configuration_revision(self.active_config)
        )
        return self.settings_state()


def create_settings_router(controller: RuntimeSettingsController) -> APIRouter:
    router = APIRouter()

    def run(action):
        try:
            return action()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"local settings could not be saved: {exc}",
            ) from exc

    @router.get("/settings", response_class=HTMLResponse)
    def settings_page() -> HTMLResponse:
        return HTMLResponse(
            (STATIC_DIR / "settings.html").read_text(encoding="utf-8")
        )

    @router.get("/api/service")
    def service_state():
        return controller.service_state()

    @router.get("/api/settings")
    def settings_state():
        return run(controller.settings_state)

    @router.put("/api/settings")
    def save_settings(payload: Mapping[str, object]):
        return run(lambda: controller.save(payload))

    @router.post("/api/settings/reset")
    def reset_settings():
        return run(controller.reset)

    return router


__all__ = [
    "FIELD_INDEX",
    "SETTINGS_SCHEMA_VERSION",
    "SETTING_GROUPS",
    "RuntimeSettingsController",
    "create_settings_router",
]
