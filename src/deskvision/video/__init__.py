"""Windows camera capture and latest-frame video-layer components."""

from deskvision.video.capture import CaptureStopError, CaptureThread
from deskvision.video.latest_frame import LatestFrameStore
from deskvision.video.windows_camera import WindowsCameraSource

__all__ = [
    "CaptureStopError",
    "CaptureThread",
    "LatestFrameStore",
    "WindowsCameraSource",
]
