"""Common lifecycle contract for long-lived components."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Lifecycle(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...
