"""Mac camera capture and latest-frame video-layer components."""

from deskvision.video.capture import CaptureStopError, CaptureThread
from deskvision.video.latest_frame import LatestFrameStore
from deskvision.video.mac_camera import MacCameraSource

__all__ = ["CaptureStopError", "CaptureThread", "LatestFrameStore", "MacCameraSource"]
