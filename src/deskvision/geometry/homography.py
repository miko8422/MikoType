"""Homography contract reserved for manual keyboard calibration."""

from typing import Protocol


class HomographyTransform(Protocol):
    def map_point(self, x: float, y: float) -> tuple[float, float]: ...
