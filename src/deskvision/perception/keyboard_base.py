"""Typed production keyboard-pose boundary."""

from typing import Protocol

from deskvision.core.models import FramePacket
from deskvision.perception.aruco_keyboard import KeyboardPoseEstimate


class KeyboardLocator(Protocol):
    def locate(self, frame: FramePacket) -> KeyboardPoseEstimate: ...


__all__ = ["KeyboardLocator"]
