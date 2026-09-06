"""Bounded latest-state storage for realtime and diagnostic consumers."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Condition, Lock
from typing import Protocol

from deskvision.state.bundle import LatestFrameStateBundleStore
from deskvision.state.scene_state import SceneState


class SceneStateStore(Protocol):
    def publish(self, state: SceneState) -> None: ...

    def latest(self) -> SceneState | None: ...


@dataclass(frozen=True, slots=True)
class LatestSceneStateStats:
    """Counters for the one-slot state store; no historical state is retained."""

    published_count: int
    overwritten_count: int
    generation: int
    latest_frame_id: int | None


class LatestSceneStateStore:
    """Keep exactly one SceneState and let consumers skip superseded states."""

    def __init__(self) -> None:
        self._condition = Condition(Lock())
        self._latest: SceneState | None = None
        self._generation = 0
        self._published_count = 0
        self._overwritten_count = 0
        # The production perception worker publishes the processed image and
        # state here as one exact-frame unit.  Keeping the store attached to
        # this already-shared state store avoids a second composition-root
        # dependency while retaining an independent, single-slot contract.
        self.bundles = LatestFrameStateBundleStore()

    def publish(self, state: SceneState) -> None:
        if not isinstance(state, SceneState):
            raise TypeError("LatestSceneStateStore accepts SceneState instances only")
        with self._condition:
            if self._latest is not None:
                self._overwritten_count += 1
            self._latest = state
            self._generation += 1
            self._published_count += 1
            self._condition.notify_all()

    def latest(self) -> SceneState | None:
        with self._condition:
            return self._latest

    def latest_with_generation(self) -> tuple[int, SceneState | None]:
        with self._condition:
            return self._generation, self._latest

    def wait_for_newer(
        self,
        after_generation: int,
        *,
        timeout_s: float | None = None,
    ) -> tuple[int, SceneState | None]:
        if isinstance(after_generation, bool) or after_generation < 0:
            raise ValueError("after_generation must be a non-negative integer")
        if timeout_s is not None and timeout_s < 0:
            raise ValueError("timeout_s must be non-negative or None")
        with self._condition:
            self._condition.wait_for(
                lambda: self._generation > after_generation,
                timeout=timeout_s,
            )
            return self._generation, self._latest

    def stats(self) -> LatestSceneStateStats:
        with self._condition:
            return LatestSceneStateStats(
                published_count=self._published_count,
                overwritten_count=self._overwritten_count,
                generation=self._generation,
                latest_frame_id=(
                    None if self._latest is None else self._latest.source_frame_id
                ),
            )


__all__ = [
    "LatestSceneStateStats",
    "LatestSceneStateStore",
    "SceneStateStore",
]
