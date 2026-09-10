"""Explicit local camera controls; status reads never acquire camera devices."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import Lock

from fastapi import APIRouter, HTTPException


CAMERA_SCHEMA_VERSION = "mikotype-camera-0.1"
CALIBRATION_WARNING = (
    "切换摄像头或改变相机位置后，请重新检查并锁定 Marker reference；"
    "若视角、分辨率或指尖投影发生变化，需要重新验证键位校准。"
)


@dataclass(slots=True)
class CameraController:
    """Adapt runtime callbacks without importing camera or inference backends.

    Callbacks return JSON-compatible state with ``current``, ``devices``,
    ``available_backends`` and ``platform``. Apply must own the runtime stop /
    restart, persistence and failure recovery transaction. Scan is explicit
    because a backend may briefly open devices or request OS permission.
    """

    status: Callable[[], Mapping[str, object]]
    scan: Callable[[], Mapping[str, object]]
    apply: Callable[[int, str], Mapping[str, object]]
    apply_view: Callable[[bool, bool], Mapping[str, object]] | None = None
    apply_orientation: Callable[[bool, bool], Mapping[str, object]] | None = None
    _action_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def _state(self, result: Mapping[str, object]) -> dict[str, object]:
        return {
            **result,
            "schema_version": CAMERA_SCHEMA_VERSION,
            "calibration_warning": CALIBRATION_WARNING,
            "busy": self._action_lock.locked(),
        }

    def get_state(self) -> dict[str, object]:
        return self._state(self.status())

    def _mutate(self, action: Callable[[], Mapping[str, object]]) -> dict[str, object]:
        if not self._action_lock.acquire(blocking=False):
            raise RuntimeError("另一个摄像头操作尚未完成，请稍后重试。")
        try:
            result = action()
        finally:
            self._action_lock.release()
        return self._state(result)

    def scan_devices(self) -> dict[str, object]:
        return self._mutate(self.scan)

    def select(self, payload: Mapping[str, object]) -> dict[str, object]:
        if set(payload) != {"device_index", "backend"}:
            raise ValueError("camera selection requires only device_index and backend")
        index, backend = payload["device_index"], payload["backend"]
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 32:
            raise ValueError("camera device_index must be an integer from 0 to 32")
        if not isinstance(backend, str) or backend not in {"any", "avfoundation", "msmf", "dshow"}:
            raise ValueError("camera backend must be any, avfoundation, msmf, or dshow")
        return self._mutate(lambda: self.apply(index, backend))

    def set_view(self, payload: Mapping[str, object]) -> dict[str, object]:
        return self._set_flags(payload, ("mirror_preview", "flip_vertical_preview"), self.apply_view)

    def set_orientation(self, payload: Mapping[str, object]) -> dict[str, object]:
        return self._set_flags(payload, ("mirror", "flip_vertical"), self.apply_orientation)

    def _set_flags(self, payload, names, callback) -> dict[str, object]:
        if set(payload) != set(names) or any(not isinstance(payload[name], bool) for name in names):
            raise ValueError(f"camera options require exactly two boolean fields: {', '.join(names)}")
        if callback is None:
            raise RuntimeError("camera orientation controls are unavailable in this runtime")
        return self._mutate(lambda: callback(*(payload[name] for name in names)))


def create_camera_router(controller: CameraController) -> APIRouter:
    router = APIRouter()

    def run(action):
        try:
            return action()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"camera unavailable: {exc}") from exc

    @router.get("/api/camera")
    def camera_state():
        return run(controller.get_state)

    @router.post("/api/camera/scan")
    def scan_cameras():
        return run(controller.scan_devices)

    @router.post("/api/camera/select")
    def select_camera(payload: Mapping[str, object]):
        return run(lambda: controller.select(payload))

    @router.post("/api/camera/view")
    def set_camera_view(payload: Mapping[str, object]):
        return run(lambda: controller.set_view(payload))

    @router.post("/api/camera/orientation")
    def set_camera_orientation(payload: Mapping[str, object]):
        return run(lambda: controller.set_orientation(payload))

    return router


__all__ = ["CameraController", "create_camera_router"]
