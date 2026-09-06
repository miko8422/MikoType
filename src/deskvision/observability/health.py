"""Health snapshot data contract for capture and future API consumers."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    status: str
    camera_open: bool
    capture_fps: float
    frame_id: int | None
    frame_age_ms: float | None
    resolution: tuple[int, int] | None
    read_failures: int = 0
    last_error: str | None = None
    frames_captured: int = 0
    overwritten_frames: int = 0

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready health representation."""
        return {
            "status": self.status,
            "camera_open": self.camera_open,
            "capture_fps": self.capture_fps,
            "frame_id": self.frame_id,
            "frame_age_ms": self.frame_age_ms,
            "resolution": None if self.resolution is None else list(self.resolution),
            "read_failures": self.read_failures,
            "last_error": self.last_error,
            "frames_captured": self.frames_captured,
            "overwritten_frames": self.overwritten_frames,
        }
