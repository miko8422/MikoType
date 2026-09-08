"""Camera control for the shared local runtime, independent of SteamVR."""

from dataclasses import replace
from threading import RLock

from deskvision.calibration.control_plane import KeyboardSetupController
from deskvision.core.platform import validate_camera_backend
from deskvision.runtime import DeskVisionRuntime
from deskvision.video.devices import enumerate_camera_devices
from deskvision.web.settings import RuntimeSettingsController


class RuntimeCameraSession:
    def __init__(self, runtime: DeskVisionRuntime, settings: RuntimeSettingsController,
                 setup: KeyboardSetupController) -> None:
        self.runtime = runtime
        self.settings = settings
        self.setup = setup
        self._devices: list[dict[str, object]] = []
        self._lock = RLock()
        self._error: str | None = None

    def status(self) -> dict[str, object]:
        with self._lock:
            camera = self.runtime.config.camera
            running = self.runtime.capture.is_running and self.runtime.source.is_open
            return {
                "platform": self.runtime.config.deployment.target_os,
                "available_backends": (
                    ["avfoundation", "any"]
                    if self.runtime.config.deployment.target_os == "macos"
                    else ["msmf", "dshow", "any"]
                ),
                "current": {
                    "device_index": camera.device_index,
                    "backend": camera.backend,
                    "name": next((item["name"] for item in self._devices
                                  if item["device_index"] == camera.device_index),
                                 f"Camera {camera.device_index}"),
                    "running": running,
                },
                "devices": self._devices,
                "status": self.runtime.status,
                "error": self._error or self.runtime.source.last_error,
                "calibration_revalidation_required": self.setup.camera_revalidation_path.exists(),
                "message": "视频采集已启动" if running else "视频未启动，请选择摄像头并应用/重试。",
            }

    def scan(self) -> dict[str, object]:
        with self._lock:
            camera = self.runtime.config.camera
            devices = enumerate_camera_devices(
                camera,
                active_device_index=camera.device_index if self.runtime.source.is_open else None,
            )
            self._devices = [{
                "device_index": item["device_index"],
                "backend": camera.backend,
                "name": item["label"],
            } for item in devices]
            return self.status()

    def apply(self, device_index: int, backend: str) -> dict[str, object]:
        with self._lock:
            validate_camera_backend(self.runtime.config.deployment.target_os, backend)
            camera = replace(self.runtime.config.camera, device_index=device_index, backend=backend)
            self._error = None
            try:
                with self.setup.camera_change(camera):
                    self.runtime.reconfigure_camera(camera)
                self.settings.accept_active_camera(device_index, backend)
                # A failed hardware open must not replace the last saved choice.
                try:
                    self.settings.save({"values": {"camera": {
                        "device_index": device_index, "backend": backend,
                    }}})
                except Exception as exc:
                    raise RuntimeError(
                        f"camera applied but settings were not saved: {exc}; "
                        "the current camera is running, but a restart uses the last saved choice"
                    ) from exc
            except Exception as exc:
                self._error = str(exc)
                raise
            return self.status()
