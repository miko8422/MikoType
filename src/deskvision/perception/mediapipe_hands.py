"""Production MediaPipe Hand Landmarker implementation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from threading import Lock
import tempfile
import time
from typing import Any

import cv2

from deskvision.core.models import FramePacket
from deskvision.perception.hand_base import (
    DetectedHand,
    HandLandmark,
    HandTrackerInitializationError,
    HandTrackerUnavailableError,
    HandTrackingResult,
)


DEFAULT_MODEL_PATH = Path(__file__).parent / "assets" / "hand_landmarker.task"


def _is_unit_interval(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and 0.0 <= float(value) <= 1.0
    )


@dataclass(frozen=True, slots=True)
class MediaPipeHandTrackerConfig:
    """Small production configuration surface for the selected hand model."""

    model_path: str | Path = DEFAULT_MODEL_PATH
    num_hands: int = 2
    min_hand_detection_confidence: float = 0.5
    min_hand_presence_confidence: float = 0.5
    min_tracking_confidence: float = 0.5
    metrics_acknowledged: bool = False

    def __post_init__(self) -> None:
        if (
            isinstance(self.num_hands, bool)
            or not isinstance(self.num_hands, int)
            or self.num_hands <= 0
        ):
            raise ValueError("num_hands must be a positive integer")
        for name, value in (
            ("min_hand_detection_confidence", self.min_hand_detection_confidence),
            ("min_hand_presence_confidence", self.min_hand_presence_confidence),
            ("min_tracking_confidence", self.min_tracking_confidence),
        ):
            if not _is_unit_interval(value):
                raise ValueError(f"{name} must be between 0 and 1")


class MediaPipeHandTracker:
    """Track up to two hands from sequential in-process BGR frames."""

    model_id = "mediapipe_hands"

    def __init__(
        self,
        config: MediaPipeHandTrackerConfig | None = None,
        **overrides: Any,
    ) -> None:
        if config is not None and overrides:
            raise ValueError("pass either config or keyword overrides, not both")
        self.config = config or MediaPipeHandTrackerConfig(**overrides)
        if self.config.metrics_acknowledged is not True:
            raise HandTrackerUnavailableError(
                "MediaPipe Tasks sends performance/utilization metrics. Explicit "
                "user acknowledgement is required before production initialization."
            )

        model_path = Path(self.config.model_path)
        if not model_path.is_file():
            raise HandTrackerUnavailableError(
                f"MediaPipe Hand Landmarker asset is missing: {model_path}"
            )

        self._mp, self._landmarker = self._load_runtime(model_path, self.config)
        self._last_timestamp_ms = -1
        self._lock = Lock()
        self._closed = False

    @staticmethod
    def _load_runtime(model_path: Path, config: MediaPipeHandTrackerConfig):
        cache_root = Path(tempfile.gettempdir()) / "vr-desk-vision-mediapipe-cache"
        cache_root.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(cache_root / "matplotlib"))
        os.environ.setdefault("XDG_CACHE_HOME", str(cache_root))
        try:
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision
        except ImportError as exc:
            raise HandTrackerUnavailableError(
                "mediapipe==0.10.35 is not installed in the production environment"
            ) from exc

        options = vision.HandLandmarkerOptions(
            base_options=python.BaseOptions(
                model_asset_path=str(model_path),
                delegate=python.BaseOptions.Delegate.CPU,
            ),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=config.num_hands,
            min_hand_detection_confidence=config.min_hand_detection_confidence,
            min_hand_presence_confidence=config.min_hand_presence_confidence,
            min_tracking_confidence=config.min_tracking_confidence,
        )
        try:
            landmarker = vision.HandLandmarker.create_from_options(options)
        except (RuntimeError, ValueError) as exc:
            raise HandTrackerInitializationError(
                f"MediaPipe Hand Landmarker initialization failed: {exc}"
            ) from exc
        return mp, landmarker

    def reset(self) -> None:
        """Forget VIDEO-mode temporal tracking after an explicit camera change."""
        with self._lock:
            if self._closed:
                raise RuntimeError("MediaPipeHandTracker is closed")
            mp, replacement = self._load_runtime(Path(self.config.model_path), self.config)
            try:
                self._landmarker.close()
            except Exception:
                replacement.close()
                raise
            self._mp, self._landmarker = mp, replacement
            self._last_timestamp_ms = -1

    def track(self, frame: FramePacket) -> HandTrackingResult:
        with self._lock:
            if self._closed:
                raise RuntimeError("MediaPipeHandTracker is closed")
            started_ns = time.perf_counter_ns()
            timestamp_ms = max(
                frame.acquired_at_ns // 1_000_000,
                self._last_timestamp_ms + 1,
            )
            self._last_timestamp_ms = timestamp_ms
            rgb = cv2.cvtColor(frame.image_bgr, cv2.COLOR_BGR2RGB)
            image = self._mp.Image(
                image_format=self._mp.ImageFormat.SRGB,
                data=rgb,
            )
            raw_result = self._landmarker.detect_for_video(image, timestamp_ms)
            hands = self.convert_result(raw_result)
            completed_ns = time.perf_counter_ns()
        return HandTrackingResult(
            model_id=self.model_id,
            source_id=frame.source_id,
            frame_id=frame.frame_id,
            acquired_at_ns=frame.acquired_at_ns,
            inference_started_ns=started_ns,
            inference_completed_ns=completed_ns,
            hands=hands,
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._landmarker.close()
            self._closed = True

    @staticmethod
    def convert_result(raw_result: object) -> tuple[DetectedHand, ...]:
        converted: list[DetectedHand] = []
        image_hands = tuple(getattr(raw_result, "hand_landmarks", ()) or ())
        world_hands = tuple(getattr(raw_result, "hand_world_landmarks", ()) or ())
        handedness = tuple(getattr(raw_result, "handedness", ()) or ())
        for index, image_landmarks in enumerate(image_hands):
            categories = handedness[index] if index < len(handedness) else ()
            category = categories[0] if categories else None
            label = (
                getattr(category, "category_name", None)
                or getattr(category, "display_name", None)
                or "unknown"
            ).lower()
            if label not in {"left", "right"}:
                label = "unknown"
            hand_score = float(getattr(category, "score", 0.0)) if category else 0.0
            image_points = tuple(
                HandLandmark(
                    x=float(point.x),
                    y=float(point.y),
                    z=float(point.z),
                    score=hand_score,
                )
                for point in image_landmarks
            )
            world_points = ()
            if index < len(world_hands):
                world_points = tuple(
                    HandLandmark(
                        x=float(point.x),
                        y=float(point.y),
                        z=float(point.z),
                        score=hand_score,
                    )
                    for point in world_hands[index]
                )
            converted.append(
                DetectedHand(
                    handedness=label,
                    score=hand_score,
                    landmarks=image_points,
                    world_landmarks=world_points,
                )
            )
        return tuple(converted)
