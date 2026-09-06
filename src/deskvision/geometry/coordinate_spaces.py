"""Named coordinate spaces shared by future geometry modules."""

from enum import StrEnum


class CoordinateSpace(StrEnum):
    IMAGE = "image"
    KEYBOARD = "keyboard"
    VR_WORLD = "vr_world"
