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
                    "mirror": camera.mirror,
                    "flip_vertical": camera.flip_vertical,
                },
                "view": {
                    "mirror_preview": self.runtime.config.debug_ui.mirror_preview,
                    "flip_vertical_preview": self.runtime.config.debug_ui.flip_vertical_preview,
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
            return self._apply_camera(camera)

    def apply_orientation(self, mirror: bool, flip_vertical: bool) -> dict[str, object]:
        with self._lock:
            camera = replace(self.runtime.config.camera, mirror=mirror, flip_vertical=flip_vertical)
            return self._apply_camera(camera)

    def apply_view(self, mirror_preview: bool, flip_vertical_preview: bool) -> dict[str, object]:
        """Persist before changing the view; never touch camera or calibration."""
        with self._lock:
            view = {"mirror_preview": mirror_preview, "flip_vertical_preview": flip_vertical_preview}
            # Dataclass validation applies to non-HTTP callers too.
            debug_ui = replace(self.runtime.config.debug_ui, **view)
            self.settings.save({"values": {"debug_ui": view}})
            self.runtime.config = replace(self.runtime.config, debug_ui=debug_ui)
            self.runtime.web_app.state.camera_view = view
            self.settings.accept_active_view(**view)
            return self.status()

    def _apply_camera(self, camera) -> dict[str, object]:
        with self._lock:
            self._error = None
            try:
                with self.setup.camera_change(camera):
                    self.runtime.reconfigure_camera(camera)
                self.settings.accept_active_camera(camera.device_index, camera.backend,
                                                  mirror=camera.mirror, flip_vertical=camera.flip_vertical)
                # A failed hardware open must not replace the last saved choice.
                try:
                    self.settings.save({"values": {"camera": {
                        "device_index": camera.device_index, "backend": camera.backend,
                        "mirror": camera.mirror, "flip_vertical": camera.flip_vertical,
                    }}})
                except Exception as exc:
                    raise RuntimeError(
                        f"camera applied but settings were not saved: {exc}; "
                        "the current camera is running, but a restart uses the last saved choice"
                    ) from exc
            except Exception as exc:
                self._error = str(exc)
                # A failed stop/configure may leave the *previous* source live.
                # The setup transaction had already recorded the requested
                # orientation; restore its identity to what runtime actually
                # retained, without clearing the recalibration safety markers.
                if self.setup.camera_config != self.runtime.config.camera:
                    try:
                        with self.setup.camera_change(self.runtime.config.camera):
                            pass
                    except Exception as reconcile_error:
                        self._error += f"; camera calibration identity recovery failed: {reconcile_error}"
                        exc.add_note(self._error)
                raise
            return self.status()
