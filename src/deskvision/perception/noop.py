"""Explicit empty perception implementation for V0.1."""

from deskvision.core.models import FramePacket
from deskvision.perception.base import PerceptionObservation


class NoOpPerceptionModule:
    @property
    def name(self) -> str:
        return "noop"

    def observe(self, frame: FramePacket) -> PerceptionObservation:
        del frame
        return {}
