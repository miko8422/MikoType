"""Local SceneState publication backing the debug WebSocket endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from deskvision.state.scene_state import SceneState
from deskvision.state.store import LatestSceneStateStore


@dataclass(frozen=True, slots=True)
class LocalPublisherStats:
    published_count: int
    last_frame_id: int | None


class LocalWebSocketPublisher:
    """Publish into a bounded store consumed by local WebSocket clients.

    The publisher deliberately owns no client queue and performs no event-loop
    work from the perception thread.  Each WebSocket waits for the next store
    generation and therefore skips superseded states when it is slow.
    """

    def __init__(self, store: LatestSceneStateStore) -> None:
        self.store = store
        self._lock = Lock()
        self._published_count = 0
        self._last_frame_id: int | None = None

    def publish(self, state: SceneState) -> None:
        # The runtime may expose the same store both as its authoritative
        # state sink and as this publisher's WebSocket backing store.
        if self.store.latest() is not state:
            self.store.publish(state)
        with self._lock:
            self._published_count += 1
            self._last_frame_id = state.source_frame_id

    def stats(self) -> LocalPublisherStats:
        with self._lock:
            return LocalPublisherStats(
                published_count=self._published_count,
                last_frame_id=self._last_frame_id,
            )


__all__ = ["LocalPublisherStats", "LocalWebSocketPublisher"]
