"""Frame preprocessing interface for rotate/resize/crop adapters."""

from typing import Protocol

from deskvision.core.models import FramePacket


class FramePreprocessor(Protocol):
    def process(self, frame: FramePacket) -> FramePacket: ...
