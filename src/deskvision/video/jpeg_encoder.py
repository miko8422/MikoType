"""Thread-safe latest-frame JPEG encoding for the optional debug preview."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol

import cv2

from deskvision.core.models import FramePacket


class PreviewEncoder(Protocol):
    def encode(self, frame: FramePacket) -> bytes: ...


@dataclass(frozen=True, slots=True)
class JpegEncoderStats:
    encoded_count: int
    cache_hit_count: int
    latest_frame_id: int | None


class LatestJpegEncoder:
    """Encode once per source frame and reuse the immutable JPEG bytes."""

    def __init__(self, *, quality: int = 80, max_width: int | None = None) -> None:
        if not 1 <= quality <= 100:
            raise ValueError("JPEG quality must be between 1 and 100")
        if max_width is not None and max_width <= 0:
            raise ValueError("max_width must be positive or None")
        self.quality = quality
        self.max_width = max_width
        self._lock = Lock()
        self._key: tuple[str, int, int] | None = None
        self._jpeg: bytes | None = None
        self._encoded_count = 0
        self._cache_hit_count = 0

    def encode(self, frame: FramePacket) -> bytes:
        key = (frame.source_id, frame.frame_id, frame.acquired_at_ns)
        with self._lock:
            if key == self._key and self._jpeg is not None:
                self._cache_hit_count += 1
                return self._jpeg

            image = frame.image_bgr
            if self.max_width is not None and frame.width > self.max_width:
                scale = self.max_width / frame.width
                image = cv2.resize(
                    image,
                    (self.max_width, max(1, round(frame.height * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            ok, encoded = cv2.imencode(
                ".jpg",
                image,
                [int(cv2.IMWRITE_JPEG_QUALITY), self.quality],
            )
            if not ok:
                raise RuntimeError("OpenCV failed to encode the preview frame")
            jpeg = encoded.tobytes()
            self._key = key
            self._jpeg = jpeg
            self._encoded_count += 1
            return jpeg

    def stats(self) -> JpegEncoderStats:
        with self._lock:
            return JpegEncoderStats(
                encoded_count=self._encoded_count,
                cache_hit_count=self._cache_hit_count,
                latest_frame_id=None if self._key is None else self._key[1],
            )


__all__ = ["JpegEncoderStats", "LatestJpegEncoder", "PreviewEncoder"]
