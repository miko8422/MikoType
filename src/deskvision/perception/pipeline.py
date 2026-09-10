"""Single-process production mapping pipeline."""

from __future__ import annotations

from dataclasses import dataclass, replace
import time
from typing import Callable, Protocol, Sequence

from deskvision.core.models import FramePacket
from deskvision.keyboard.interaction import KeyHighlight, aggregate_key_highlights
from deskvision.observability.health import HealthSnapshot
from deskvision.perception.aruco_keyboard import (
    ArucoKeyboardLocator,
    KeyboardPoseEstimate,
)
from deskvision.perception.hand_base import HandTracker, HandTrackingResult
from deskvision.perception.key_candidates import ContactKeyMapper, KeyCandidateResult
from deskvision.state.scene_state import (
    ArtifactRevisions,
    Diagnostics,
    FingertipState,
    KeyCandidateState,
    KeyHighlightState,
    KeyboardModelState,
    KeyboardPoseState,
    KeyboardState,
    SCENE_STATE_SCHEMA_VERSION,
    SceneState,
)


class PerceptionPipeline(Protocol):
    def process(self, frame: FramePacket) -> SceneState: ...


@dataclass(frozen=True, slots=True)
class MappingPipelineConfig:
    neighbor_glow_enabled: bool = True
    neighbor_glow_scale: float = 0.55
    direct_min_intensity: float = 0.35

    def __post_init__(self) -> None:
        if self.neighbor_glow_scale <= 0:
            raise ValueError("neighbor_glow_scale must be positive")
        if not 0 <= self.direct_min_intensity <= 1:
            raise ValueError("direct_min_intensity must be between 0 and 1")


class ProductionMappingPipeline:
    """Run hands, ArUco pose, and measured contact mapping on one raw frame."""

    def __init__(
        self,
        *,
        hand_tracker: HandTracker,
        keyboard_locator: ArucoKeyboardLocator,
        key_mapper: ContactKeyMapper,
        artifact_revisions: ArtifactRevisions,
        model: KeyboardModelState,
        capture_metrics: Callable[[], HealthSnapshot] | None = None,
        config: MappingPipelineConfig | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        mapping_guard: Callable[[], str | None] | None = None,
    ) -> None:
        self.hand_tracker = hand_tracker
        self.keyboard_locator = keyboard_locator
        self.key_mapper = key_mapper
        self.artifact_revisions = artifact_revisions
        self.model = model
        self.capture_metrics = capture_metrics
        self.config = config or MappingPipelineConfig()
        self.clock_ns = clock_ns
        self.monotonic_ns = monotonic_ns
        # This callback reads a cached camera/calibration gate only. It must not
        # acquire the setup lock (camera changes join this worker under it).
        self.mapping_guard = mapping_guard
        self._closed = False

    def _mapping_block_reason(self) -> str | None:
        if self.mapping_guard is None:
            return None
        try:
            return self.mapping_guard()
        except Exception:
            return "calibration guard unavailable; keyboard mapping disabled"

    def process(self, frame: FramePacket) -> SceneState:
        if self._closed:
            raise RuntimeError("production mapping pipeline is closed")
        started_ns = self.monotonic_ns()
        hand_result: HandTrackingResult | None = None
        pose: KeyboardPoseEstimate | None = None
        candidates: KeyCandidateResult | None = None
        errors: list[str] = []

        hand_started_ns = self.monotonic_ns()
        try:
            tracked_hands = self.hand_tracker.track(frame)
            self._validate_identity(frame, tracked_hands)
            hand_result = tracked_hands
        except Exception as exc:
            errors.append(f"hand:{type(exc).__name__}:{exc}")
        hand_duration_ms = (self.monotonic_ns() - hand_started_ns) / 1_000_000.0

        keyboard_started_ns = self.monotonic_ns()
        try:
            located_pose = self.keyboard_locator.locate(frame)
            self._validate_identity(frame, located_pose)
            pose = located_pose
        except Exception as exc:
            errors.append(f"keyboard:{type(exc).__name__}:{exc}")

        mapping_block = self._mapping_block_reason()
        if mapping_block:
            errors.append(f"mapping:calibration_required:{mapping_block}")
        elif hand_result is not None and pose is not None and pose.usable:
            try:
                mapped_candidates = self.key_mapper.map(frame, hand_result, pose)
                self._validate_identity(frame, mapped_candidates)
                candidates = mapped_candidates
                if not mapped_candidates.usable:
                    errors.append(
                        "mapping:"
                        f"{mapped_candidates.status}:"
                        f"{mapped_candidates.reason or 'unusable'}"
                    )
            except Exception as exc:
                errors.append(f"mapping:{type(exc).__name__}:{exc}")
        elif pose is not None and not pose.usable:
            errors.append(f"mapping:pose_unusable:{pose.reason or pose.status}")
        keyboard_duration_ms = (
            self.monotonic_ns() - keyboard_started_ns
        ) / 1_000_000.0

        # A camera change can begin while this frame is in inference. Recheck
        # before publishing so a late old-calibration result fails closed.
        mapping_block = mapping_block or self._mapping_block_reason()
        if mapping_block:
            candidates = None
        fingertips = self._fingertip_states(candidates)
        highlights = self._highlight_states(candidates)
        pose_state = self._pose_state(pose)
        if mapping_block:
            pose_state = replace(pose_state, status="calibration_required", usable=False, confidence=0.0)
        keyboard = KeyboardState(
            coordinate_space="aruco-anchor-reference-2d",
            pose=pose_state,
            artifacts=self.artifact_revisions,
            model=self.model,
        )
        capture = self.capture_metrics() if self.capture_metrics is not None else None
        frame_age_ms = max(0.0, (self.clock_ns() - frame.acquired_at_ns) / 1_000_000.0)
        status = "ready"
        if mapping_block:
            status = "calibration_required"
            if not any("mapping:calibration_required:" in error for error in errors):
                errors.append(f"mapping:calibration_required:{mapping_block}")
        elif errors:
            status = "degraded"
        elif not pose_state.usable:
            status = "keyboard_unavailable"
        total_ms = (self.monotonic_ns() - started_ns) / 1_000_000.0
        diagnostics = Diagnostics(
            capture_fps=0.0 if capture is None else capture.capture_fps,
            frame_age_ms=frame_age_ms,
            hand_inference_ms=(
                hand_duration_ms
                if hand_result is None
                else max(hand_duration_ms, hand_result.latency_ms)
            ),
            keyboard_inference_ms=keyboard_duration_ms,
            perception_ms=total_ms,
            status=status,
            error=" | ".join(errors) if errors else None,
        )
        return SceneState(
            schema_version=SCENE_STATE_SCHEMA_VERSION,
            source_id=frame.source_id,
            source_frame_id=frame.frame_id,
            captured_at_ns=frame.acquired_at_ns,
            emitted_at_ns=self.clock_ns(),
            keyboard=keyboard,
            hands=() if hand_result is None else hand_result.hands,
            fingertips=fingertips,
            key_highlights=highlights,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _validate_identity(frame: FramePacket, result: object) -> None:
        identity = (
            getattr(result, "source_id", None),
            getattr(result, "frame_id", None),
            getattr(result, "acquired_at_ns", None),
        )
        expected = (frame.source_id, frame.frame_id, frame.acquired_at_ns)
        if identity != expected:
            raise ValueError(
                f"same-frame identity mismatch: expected={expected}, received={identity}"
            )

    @staticmethod
    def _pose_state(pose: KeyboardPoseEstimate | None) -> KeyboardPoseState:
        if pose is None:
            return KeyboardPoseState(
                status="error",
                usable=False,
                confidence=0.0,
                anchor_count=0,
            )
        matrix = None
        if pose.reference_to_image is not None:
            matrix = tuple(value for row in pose.reference_to_image for value in row)
        return KeyboardPoseState(
            status=pose.status,
            usable=pose.usable,
            confidence=pose.confidence,
            anchor_count=len(pose.marker_ids),
            detected_marker_ids=pose.marker_ids,
            reference_to_image=matrix,
            reprojection_rmse_px=pose.reprojection_rmse_px,
            coast_age_ms=pose.coast_age_ms,
        )

    @staticmethod
    def _fingertip_states(
        result: KeyCandidateResult | None,
    ) -> tuple[FingertipState, ...]:
        if result is None or not result.usable:
            return ()
        output: list[FingertipState] = []
        for prediction in result.predictions:
            output.append(
                FingertipState(
                    bubble_id=prediction.bubble_id,
                    hand_index=prediction.hand_index,
                    handedness=prediction.handedness,
                    finger=prediction.finger,
                    confidence=prediction.confidence,
                    image_x=prediction.image_x,
                    image_y=prediction.image_y,
                    reference_x=prediction.reference_x,
                    reference_y=prediction.reference_y,
                    pose_confidence=prediction.pose_confidence,
                    candidates=tuple(
                        KeyCandidateState(
                            physical_key_id=candidate.physical_key_id,
                            model_node_id=f"key:{candidate.physical_key_id}",
                            label=candidate.label,
                            rank=candidate.rank,
                            distance=candidate.distance_reference,
                            probability=candidate.probability,
                            direct=candidate.direct,
                        )
                        for candidate in prediction.candidates
                    ),
                )
            )
        return tuple(output)

    def _highlight_states(
        self,
        result: KeyCandidateResult | None,
    ) -> tuple[KeyHighlightState, ...]:
        if result is None or not result.usable:
            return ()
        raw: list[KeyHighlight] = []
        labels: dict[str, str] = {}
        for prediction in result.predictions:
            for candidate in prediction.candidates:
                intensity = candidate.probability
                if candidate.rank > 1:
                    if not self.config.neighbor_glow_enabled:
                        continue
                    intensity *= self.config.neighbor_glow_scale
                intensity = max(0.0, min(1.0, intensity))
                direct = (
                    candidate.direct
                    and intensity >= self.config.direct_min_intensity
                )
                labels.setdefault(candidate.physical_key_id, candidate.label)
                raw.append(
                    KeyHighlight(
                        key_id=candidate.physical_key_id,
                        intensity=intensity,
                        direct=direct,
                        contributors=(prediction.bubble_id,),
                    )
                )
        return tuple(
            KeyHighlightState(
                physical_key_id=highlight.key_id,
                model_node_id=f"key:{highlight.key_id}",
                label=labels.get(highlight.key_id, highlight.key_id),
                intensity=highlight.intensity,
                direct=highlight.direct,
                contributors=highlight.contributors,
            )
            for highlight in aggregate_key_highlights(raw)
        )

    def close(self) -> None:
        if self._closed:
            return
        self.hand_tracker.close()
        self._closed = True


__all__ = [
    "MappingPipelineConfig",
    "PerceptionPipeline",
    "ProductionMappingPipeline",
]
