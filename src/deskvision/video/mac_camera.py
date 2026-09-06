"""OpenCV/AVFoundation camera source for the Mac video layer."""

from __future__ import annotations

from threading import RLock
import time
from types import ModuleType
from typing import Any, Callable

from deskvision.core.config import CameraConfig
from deskvision.core.models import FramePacket


class CameraSourceError(RuntimeError):
    """Base error for camera lifecycle and frame acquisition failures."""


class CameraOpenError(CameraSourceError):
    """The configured camera could not be opened."""


class CameraNotOpenError(CameraSourceError):
    """A read was attempted before a successful open."""


class CameraReadError(CameraSourceError):
    """The camera backend raised while reading a frame."""


class CameraCloseError(CameraSourceError):
    """The camera backend failed while releasing its handle."""


class MacCameraSource:
    """Read one Mac camera through OpenCV's AVFoundation backend.

    cv2 is imported only when open is called. capture_factory and cv2_module
    are test seams; production code uses cv2.VideoCapture. rotate_degrees and
    mirror remain preprocessing concerns and are not silently applied to
    algorithm coordinates here.
    """

    def __init__(
        self,
        config: CameraConfig | None = None,
        *,
        capture_factory: Callable[[int, int], Any] | None = None,
        cv2_module: ModuleType | Any | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.config = config or CameraConfig()
        self._capture_factory = capture_factory
        self._cv2_module = cv2_module
        self._clock_ns = clock_ns
        self._lock = RLock()
        self._capture: Any | None = None
        self._frame_id = 0
        self._last_error: str | None = None

    def _load_cv2(self) -> Any:
        if self._cv2_module is not None:
            return self._cv2_module
        try:
            import cv2  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise CameraOpenError(
                "opencv-python is required to open the Mac camera; "
                "install the project dependencies first"
            ) from exc
        self._cv2_module = cv2
        return cv2

    def _backend_flag(self, cv2: Any) -> int:
        if self.config.backend.lower() == "avfoundation":
            flag = getattr(cv2, "CAP_AVFOUNDATION", None)
            if flag is None:
                raise CameraOpenError("OpenCV was built without AVFoundation support")
            return int(flag)
        return int(getattr(cv2, "CAP_ANY", 0))

    def open(self) -> None:
        with self._lock:
            if self._capture is not None and self._capture_is_open(self._capture):
                return

        try:
            cv2 = self._load_cv2()
            backend_flag = self._backend_flag(cv2)
        except CameraSourceError as exc:
            self._set_error(str(exc))
            raise
        factory = self._capture_factory or cv2.VideoCapture
        try:
            capture = factory(self.config.device_index, backend_flag)
        except Exception as exc:
            self._set_error(f"camera construction failed: {exc}")
            raise CameraOpenError(
                f"could not create camera device {self.config.device_index}: {exc}"
            ) from exc

        if not self._capture_is_open(capture):
            self._release_capture(capture)
            message = (
                f"could not open camera device {self.config.device_index} "
                f"with backend {self.config.backend}"
            )
            self._set_error(message)
            raise CameraOpenError(message)

        try:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
            capture.set(cv2.CAP_PROP_FPS, self.config.fps)
        except Exception as exc:
            self._release_capture(capture)
            message = f"camera configuration failed: {exc}"
            self._set_error(message)
            raise CameraOpenError(message) from exc

        with self._lock:
            self._capture = capture
            self._last_error = None

    def read(self) -> FramePacket | None:
        with self._lock:
            capture = self._capture
        if capture is None or not self._capture_is_open(capture):
            raise CameraNotOpenError("camera is not open")

        try:
            ok, image = capture.read()
        except Exception as exc:
            message = f"camera read raised {type(exc).__name__}: {exc}"
            self._set_error(message)
            raise CameraReadError(message) from exc

        if not ok or image is None:
            message = "camera read returned no frame"
            self._set_error(message)
            return None

        shape = getattr(image, "shape", None)
        if shape is None:
            message = "camera returned an image without a 3-dimensional BGR shape"
            self._set_error(message)
            raise CameraReadError(message)
        normalized_shape = tuple(shape)
        if len(normalized_shape) != 3:
            message = "camera returned an image without a 3-dimensional BGR shape"
            self._set_error(message)
            raise CameraReadError(message)
        height, width = int(normalized_shape[0]), int(normalized_shape[1])
        try:
            frame = FramePacket(
                source_id=self.config.source_id,
                frame_id=self._next_frame_id(),
                acquired_at_ns=self._clock_ns(),
                width=width,
                height=height,
                image_bgr=image,
            )
        except (TypeError, ValueError) as exc:
            message = f"camera returned an invalid BGR frame: {exc}"
            self._set_error(message)
            raise CameraReadError(message) from exc
        self._set_error(None)
        return frame

    def close(self) -> None:
        with self._lock:
            capture = self._capture
            self._capture = None
        if capture is None:
            return
        try:
            self._release_capture(capture)
        except Exception as exc:
            message = f"camera release failed: {exc}"
            self._set_error(message)
            raise CameraCloseError(message) from exc

    @property
    def is_open(self) -> bool:
        with self._lock:
            capture = self._capture
        return capture is not None and self._capture_is_open(capture)

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def _next_frame_id(self) -> int:
        with self._lock:
            frame_id = self._frame_id
            self._frame_id += 1
            return frame_id

    def _set_error(self, error: str | None) -> None:
        with self._lock:
            self._last_error = error

    @staticmethod
    def _capture_is_open(capture: Any) -> bool:
        try:
            return bool(capture.isOpened())
        except Exception:
            return False

    @staticmethod
    def _release_capture(capture: Any) -> None:
        capture.release()
