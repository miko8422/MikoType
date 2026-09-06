"""In-process data contracts that must not cross the JSON boundary."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FramePacket:
    """Immutable metadata wrapper for one in-process BGR camera frame.

    The NumPy image is intentionally not copied. A source must publish a frame
    with a stable ``(height, width, 3)`` shape, and consumers must treat the
    array as read-only by convention so capture remains low-latency.
    """

    source_id: str
    frame_id: int
    acquired_at_ns: int
    width: int
    height: int
    image_bgr: Any

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("source_id must be a non-empty string")
        for name, value in (
            ("frame_id", self.frame_id),
            ("acquired_at_ns", self.acquired_at_ns),
            ("width", self.width),
            ("height", self.height),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
        if self.frame_id < 0:
            raise ValueError("frame_id must be non-negative")
        if self.acquired_at_ns < 0:
            raise ValueError("acquired_at_ns must be non-negative")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("frame width and height must be positive")

        shape = getattr(self.image_bgr, "shape", None)
        if shape is None:
            raise TypeError("image_bgr must expose a NumPy-like shape")
        try:
            normalized_shape = tuple(shape)
        except TypeError as exc:
            raise TypeError("image_bgr shape must be iterable") from exc
        if normalized_shape != (self.height, self.width, 3):
            raise ValueError(
                "image_bgr must have BGR shape "
                f"({self.height}, {self.width}, 3), got {normalized_shape}"
            )
