"""Atomic, bounded pairing of a processed frame and its SceneState."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Condition, Lock

from deskvision.core.models import FramePacket
from deskvision.state.scene_state import SceneState


@dataclass(frozen=True, slots=True)
class FrameStateBundle:
    """One exact source frame paired with the state derived from that frame."""

    frame: FramePacket
    state: SceneState

    def __post_init__(self) -> None:
        frame_identity = (
            self.frame.source_id,
            self.frame.frame_id,
            self.frame.acquired_at_ns,
        )
        state_identity = (
            self.state.source_id,
            self.state.source_frame_id,
            self.state.captured_at_ns,
        )
        if frame_identity != state_identity:
            raise ValueError(
                "FrameStateBundle identity mismatch: "
                f"frame={frame_identity}, state={state_identity}"
            )


@dataclass(frozen=True, slots=True)
class LatestFrameStateBundleStats:
    published_count: int
    overwritten_count: int
    generation: int
    latest_frame_id: int | None


class LatestFrameStateBundleStore:
    """Keep only the newest processed frame/state pair.

    Generation, rather than frame ID, is the cursor so source restarts and a
    fail-closed replacement for the same frame cannot strand a consumer.
    """

    def __init__(self) -> None:
        self._condition = Condition(Lock())
        self._latest: FrameStateBundle | None = None
        self._generation = 0
        self._published_count = 0
        self._overwritten_count = 0

    def publish(
        self,
        frame: FramePacket,
        state: SceneState,
    ) -> FrameStateBundle:
        bundle = FrameStateBundle(frame=frame, state=state)
        with self._condition:
            if self._latest is not None:
                self._overwritten_count += 1
            self._latest = bundle
            self._generation += 1
            self._published_count += 1
            self._condition.notify_all()
        return bundle

    def latest(self) -> FrameStateBundle | None:
        with self._condition:
            return self._latest

    def latest_with_generation(self) -> tuple[int, FrameStateBundle | None]:
        with self._condition:
            return self._generation, self._latest

    def wait_for_newer(
        self,
        after_generation: int,
        *,
        timeout_s: float | None = None,
    ) -> tuple[int, FrameStateBundle | None]:
        if (
            isinstance(after_generation, bool)
            or not isinstance(after_generation, int)
            or after_generation < 0
        ):
            raise ValueError("after_generation must be a non-negative integer")
        if timeout_s is not None and timeout_s < 0:
            raise ValueError("timeout_s must be non-negative or None")
        with self._condition:
            advanced = self._condition.wait_for(
                lambda: self._generation > after_generation,
                timeout=timeout_s,
            )
            return self._generation, self._latest if advanced else None

    def stats(self) -> LatestFrameStateBundleStats:
        with self._condition:
            return LatestFrameStateBundleStats(
                published_count=self._published_count,
                overwritten_count=self._overwritten_count,
                generation=self._generation,
                latest_frame_id=(
                    None
                    if self._latest is None
                    else self._latest.frame.frame_id
                ),
            )


__all__ = [
    "FrameStateBundle",
    "LatestFrameStateBundleStats",
    "LatestFrameStateBundleStore",
]
