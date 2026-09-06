"""Thread-safe, bounded latest-frame store for real-time consumers."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Condition, Lock

from deskvision.core.models import FramePacket


@dataclass(frozen=True, slots=True)
class LatestFrameStats:
    """Counters for the one-slot store; no image queue is retained."""

    published_count: int
    overwritten_count: int
    latest_frame_id: int | None


class LatestFrameStore:
    """Keep exactly zero or one frame and notify waiting consumers."""

    def __init__(self) -> None:
        self._condition = Condition(Lock())
        self._latest: FramePacket | None = None
        self._published_count = 0
        self._overwritten_count = 0

    def publish(self, frame: FramePacket) -> None:
        if not isinstance(frame, FramePacket):
            raise TypeError("LatestFrameStore accepts FramePacket instances only")
        with self._condition:
            if self._latest is not None:
                self._overwritten_count += 1
            self._latest = frame
            self._published_count += 1
            self._condition.notify_all()

    def latest(self) -> FramePacket | None:
        with self._condition:
            return self._latest

    def wait_for_newer(
        self,
        after_frame_id: int | None = None,
        *,
        timeout_s: float | None = None,
    ) -> FramePacket | None:
        """Return the latest frame with an id newer than after_frame_id.

        A consumer may skip intermediate frames by design. The timeout is
        bounded and does not create a secondary frame queue.
        """
        if timeout_s is not None and timeout_s < 0:
            raise ValueError("timeout_s must be non-negative or None")

        def ready() -> bool:
            return self._latest is not None and (
                after_frame_id is None or self._latest.frame_id > after_frame_id
            )

        with self._condition:
            if not ready():
                self._condition.wait_for(ready, timeout=timeout_s)
            return self._latest if ready() else None

    def stats(self) -> LatestFrameStats:
        with self._condition:
            return LatestFrameStats(
                published_count=self._published_count,
                overwritten_count=self._overwritten_count,
                latest_frame_id=None if self._latest is None else self._latest.frame_id,
            )
