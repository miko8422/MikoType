"""Synthetic FrameSource used only by the isolated test UI."""

from __future__ import annotations

import time

import cv2
import numpy as np

from deskvision.core.models import FramePacket


class SyntheticFrameSource:
    """Generate an annotated moving BGR test pattern at a bounded frame rate."""

    def __init__(
        self,
        *,
        source_id: str = "test_synthetic",
        width: int = 960,
        height: int = 540,
        fps: int = 30,
    ) -> None:
        if width <= 0 or height <= 0 or fps <= 0:
            raise ValueError("synthetic width, height, and fps must be positive")
        self.source_id = source_id
        self.width = width
        self.height = height
        self.fps = fps
        self._open = False
        self._frame_id = 0
        self._last_emit_monotonic: float | None = None
        self._last_error: str | None = None

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def open(self) -> None:
        self._open = True
        self._last_error = None

    def read(self) -> FramePacket:
        if not self._open:
            raise RuntimeError("synthetic source is not open")
        interval = 1.0 / self.fps
        if self._last_emit_monotonic is not None:
            wait_s = interval - (time.monotonic() - self._last_emit_monotonic)
            if wait_s > 0:
                time.sleep(wait_s)
        self._last_emit_monotonic = time.monotonic()

        frame_id = self._frame_id
        image = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        image[:, :] = (28, 33, 42)

        phase = frame_id % max(self.width, self.height)
        cv2.rectangle(image, (0, 0), (self.width - 1, self.height - 1), (54, 70, 90), 3)
        cv2.line(image, (phase, 0), (self.width - phase, self.height), (80, 180, 240), 3)
        cv2.line(
            image,
            (0, self.height - phase % self.height),
            (self.width, phase % self.height),
            (90, 210, 150),
            2,
        )
        cv2.circle(
            image,
            (self.width // 2, self.height // 2),
            40 + (frame_id % 80),
            (220, 150, 70),
            4,
        )
        cv2.putText(
            image,
            "SYNTHETIC TEST FRAME",
            (32, 52),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (235, 240, 245),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            image,
            f"frame_id={frame_id}  source={self.source_id}",
            (32, 90),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (190, 205, 220),
            2,
            cv2.LINE_AA,
        )
        self._frame_id += 1
        return FramePacket(
            source_id=self.source_id,
            frame_id=frame_id,
            acquired_at_ns=time.time_ns(),
            width=self.width,
            height=self.height,
            image_bgr=image,
        )

    def close(self) -> None:
        self._open = False
