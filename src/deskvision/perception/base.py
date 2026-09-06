"""Common perception module contract."""

from typing import Mapping, Protocol

from deskvision.core.models import FramePacket

PerceptionObservation = Mapping[str, object]


class PerceptionModule(Protocol):
    @property
    def name(self) -> str: ...

    def observe(self, frame: FramePacket) -> PerceptionObservation: ...
