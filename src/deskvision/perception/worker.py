"""Latest-frame-only perception scheduling.

The worker owns no image queue.  If capture publishes several frames while one
frame is being processed, the next iteration consumes only the newest frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock, Thread, current_thread
import time
from typing import Protocol, Sequence

from deskvision.core.models import FramePacket
from deskvision.state.scene_state import Diagnostics, SceneState
from deskvision.state.store import LatestSceneStateStore
from deskvision.transport.base import StatePublisher
from deskvision.video.latest_frame import LatestFrameStore


class SceneProcessor(Protocol):
    def process(self, frame: FramePacket) -> SceneState: ...


class PerceptionWorkerStopError(RuntimeError):
    """The perception worker did not terminate before its shutdown deadline."""


@dataclass(frozen=True, slots=True)
class PerceptionWorkerStats:
    status: str
    processed_count: int
    skipped_frame_count: int
    last_frame_id: int | None
    last_duration_ms: float
    last_error: str | None


class LatestFramePerceptionWorker:
    """Continuously process only the newest available raw FramePacket."""

    def __init__(
        self,
        frames: LatestFrameStore,
        processor: SceneProcessor,
        states: LatestSceneStateStore,
        *,
        publishers: Sequence[StatePublisher] = (),
        frame_wait_timeout_s: float = 0.05,
        stale_after_ms: float | None = None,
    ) -> None:
        if frame_wait_timeout_s <= 0:
            raise ValueError("frame_wait_timeout_s must be positive")
        if stale_after_ms is not None and stale_after_ms <= 0:
            raise ValueError("stale_after_ms must be positive or None")
        self.frames = frames
        self.processor = processor
        self.states = states
        self.publishers = tuple(publishers)
        self.frame_wait_timeout_s = frame_wait_timeout_s
        self.stale_after_ms = stale_after_ms
        self._stop_event = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._status = "stopped"
        self._processed_count = 0
        self._skipped_frame_count = 0
        self._last_frame_id: int | None = None
        self._last_duration_ms = 0.0
        self._last_error: str | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._status = "starting"
            self._last_error = None
            self._thread = Thread(
                target=self._run,
                name="deskvision-perception",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        if timeout_s < 0:
            raise ValueError("timeout_s must be non-negative")
        self._stop_event.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout_s)
            if thread.is_alive():
                with self._lock:
                    self._status = "shutdown_timeout"
                    self._last_error = (
                        "perception worker did not stop within "
                        f"{timeout_s:.3f}s"
                    )
                raise PerceptionWorkerStopError(self._last_error)
        with self._lock:
            if thread is None or not thread.is_alive():
                self._thread = None
                self._status = "stopped"

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def stats(self) -> PerceptionWorkerStats:
        with self._lock:
            return PerceptionWorkerStats(
                status=self._status,
                processed_count=self._processed_count,
                skipped_frame_count=self._skipped_frame_count,
                last_frame_id=self._last_frame_id,
                last_duration_ms=self._last_duration_ms,
                last_error=self._last_error,
            )

    def _run(self) -> None:
        with self._lock:
            self._status = "running"
        after_frame_id: int | None = None
        last_frame: FramePacket | None = None
        last_publication_ns: int | None = None
        stale_published = False
        while not self._stop_event.is_set():
            frame = self.frames.wait_for_newer(
                after_frame_id,
                timeout_s=self.frame_wait_timeout_s,
            )
            if frame is None or (
                after_frame_id is not None and frame.frame_id <= after_frame_id
            ):
                if (
                    self.stale_after_ms is not None
                    and last_frame is not None
                    and last_publication_ns is not None
                    and not stale_published
                    and (time.monotonic_ns() - last_publication_ns) / 1_000_000.0
                    >= self.stale_after_ms
                ):
                    self._publish_stale(last_frame)
                    stale_published = True
                continue
            if after_frame_id is not None and frame.frame_id > after_frame_id + 1:
                with self._lock:
                    self._skipped_frame_count += frame.frame_id - after_frame_id - 1
            started_ns = time.monotonic_ns()
            try:
                state = self.processor.process(frame)
                if (
                    state.source_id != frame.source_id
                    or state.source_frame_id != frame.frame_id
                    or state.captured_at_ns != frame.acquired_at_ns
                ):
                    raise ValueError(
                        "scene processor broke the same-frame source metadata contract"
                    )
                self.states.publish(state)
                for publisher in self.publishers:
                    publisher.publish(state)
                self.states.bundles.publish(frame, state)
            except Exception as exc:
                # Never leave consumers looking at highlights from an older
                # frame when a processor breaks its contract.  The failure is
                # itself published as a same-frame, empty SceneState.
                error = f"{type(exc).__name__}: {exc}"
                failed_state = SceneState.empty(
                    source_id=frame.source_id,
                    source_frame_id=frame.frame_id,
                    captured_at_ns=frame.acquired_at_ns,
                    diagnostics=Diagnostics(
                        frame_age_ms=max(
                            0.0,
                            (time.time_ns() - frame.acquired_at_ns) / 1_000_000.0,
                        ),
                        perception_ms=(
                            time.monotonic_ns() - started_ns
                        ) / 1_000_000.0,
                        status="error",
                        error=error,
                    ),
                )
                publication_errors: list[str] = []
                try:
                    self.states.publish(failed_state)
                except Exception as publish_exc:
                    publication_errors.append(
                        f"state publication failed: {type(publish_exc).__name__}: "
                        f"{publish_exc}"
                    )
                try:
                    self.states.bundles.publish(frame, failed_state)
                except Exception as publish_exc:
                    publication_errors.append(
                        "bundle publication failed: "
                        f"{type(publish_exc).__name__}: {publish_exc}"
                    )
                for publisher in self.publishers:
                    try:
                        publisher.publish(failed_state)
                    except Exception as publish_exc:
                        publication_errors.append(
                            "publisher failed: "
                            f"{type(publish_exc).__name__}: {publish_exc}"
                        )
                with self._lock:
                    self._status = "degraded"
                    self._last_error = " | ".join((error, *publication_errors))
                    self._last_frame_id = frame.frame_id
                    self._last_duration_ms = (
                        time.monotonic_ns() - started_ns
                    ) / 1_000_000.0
            else:
                with self._lock:
                    self._status = "running"
                    self._last_error = None
                    self._processed_count += 1
                    self._last_frame_id = frame.frame_id
                    self._last_duration_ms = (
                        time.monotonic_ns() - started_ns
                    ) / 1_000_000.0
            finally:
                after_frame_id = frame.frame_id
                last_frame = frame
                last_publication_ns = time.monotonic_ns()
                stale_published = False
        with self._lock:
            if self._status != "shutdown_timeout":
                self._status = "stopped"

    def _publish_stale(self, frame: FramePacket) -> None:
        error = "camera/perception input did not advance before the freshness deadline"
        state = SceneState.empty(
            source_id=frame.source_id,
            source_frame_id=frame.frame_id,
            captured_at_ns=frame.acquired_at_ns,
            diagnostics=Diagnostics(
                frame_age_ms=max(
                    0.0,
                    (time.time_ns() - frame.acquired_at_ns) / 1_000_000.0,
                ),
                status="stale",
                error=error,
            ),
        )
        publication_errors: list[str] = []
        try:
            self.states.publish(state)
            self.states.bundles.publish(frame, state)
        except Exception as exc:
            publication_errors.append(f"{type(exc).__name__}: {exc}")
        for publisher in self.publishers:
            try:
                publisher.publish(state)
            except Exception as exc:
                publication_errors.append(f"{type(exc).__name__}: {exc}")
        with self._lock:
            self._status = "degraded"
            self._last_error = " | ".join((error, *publication_errors))


__all__ = [
    "LatestFramePerceptionWorker",
    "PerceptionWorkerStats",
    "PerceptionWorkerStopError",
    "SceneProcessor",
]
