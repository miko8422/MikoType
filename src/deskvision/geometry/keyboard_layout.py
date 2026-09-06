"""Generic polygon boundary for geometry consumers.

Production keyboard inventory and measured contact geometry live in
``deskvision.calibration``.  This small type remains useful for consumers that
only need an ID plus a polygon and deliberately carries no calibration policy.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KeyRegion:
    key_id: str
    polygon: tuple[tuple[float, float], ...]
