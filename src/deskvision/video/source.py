"""Video source lifecycle contract."""

from typing import Protocol, runtime_checkable

from deskvision.core.models import FramePacket


@runtime_checkable
class FrameSource(Protocol):
    def open(self) -> None: ...

    def read(self) -> FramePacket | None: ...

    def close(self) -> None: ...

    @property
    def is_open(self) -> bool: ...
