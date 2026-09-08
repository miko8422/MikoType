"""Compatibility imports for existing Windows video and demo integrations.

The implementation is shared with macOS in ``opencv_camera``; the legacy
class name remains an alias so existing imports and subclasses keep working.
"""

from deskvision.video.opencv_camera import (
    CameraCloseError,
    CameraNotOpenError,
    CameraOpenError,
    CameraReadError,
    CameraSourceError,
    OpenCVCameraSource,
)

WindowsCameraSource = OpenCVCameraSource

__all__ = [
    "CameraCloseError",
    "CameraNotOpenError",
    "CameraOpenError",
    "CameraReadError",
    "CameraSourceError",
    "WindowsCameraSource",
]
