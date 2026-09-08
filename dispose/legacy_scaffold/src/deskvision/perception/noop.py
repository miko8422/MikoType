"""Archived empty perception scaffold; not part of the production package."""

from deskvision.core.models import FramePacket
from deskvision.perception.base import PerceptionObservation


class NoOpPerceptionModule:
    @property
    def name(self) -> str:
        return "noop"

    def observe(self, frame: FramePacket) -> PerceptionObservation:
        del frame
        return {}
