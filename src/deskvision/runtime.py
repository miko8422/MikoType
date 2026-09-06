"""Composition root for the single-host Windows V0.1 runtime."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from deskvision.calibration._storage import atomic_write_bytes, atomic_write_json
from deskvision.calibration.artifacts import (
    KeyboardCalibrationArtifacts,
    load_keyboard_calibration_artifacts,
)
from deskvision.core.config import DeskVisionConfig
from deskvision.core.platform import is_loopback_host, require_windows_runtime
from deskvision.keyboard.adaptive_model import (
    GeneratedKeyboardModel,
    generate_adaptive_keyboard_model,
)
from deskvision.perception.aruco_keyboard import (
    ArucoKeyboardLocator,
    ArucoKeyboardLocatorConfig,
    ArucoObservationDetector,
)
from deskvision.perception.hand_base import HandTracker
from deskvision.perception.key_candidates import (
    ContactKeyMapper,
    ContactKeyMapperConfig,
)
from deskvision.perception.mediapipe_hands import (
    DEFAULT_MODEL_PATH,
    MediaPipeHandTracker,
    MediaPipeHandTrackerConfig,
)
from deskvision.perception.pipeline import (
    MappingPipelineConfig,
    ProductionMappingPipeline,
)
from deskvision.perception.worker import LatestFramePerceptionWorker
from deskvision.state.scene_state import ArtifactRevisions, KeyboardModelState
from deskvision.state.store import LatestSceneStateStore
from deskvision.transport.local_websocket import LocalWebSocketPublisher
from deskvision.video.capture import CaptureThread
from deskvision.video.jpeg_encoder import LatestJpegEncoder
from deskvision.video.latest_frame import LatestFrameStore
from deskvision.video.windows_camera import WindowsCameraSource
from deskvision.video.source import FrameSource
from deskvision.web.app import DebugWebContext, create_debug_app


class RuntimeBuildError(RuntimeError):
    """Production artifacts or configuration cannot form a safe runtime."""


def validate_runtime_config(config: DeskVisionConfig) -> None:
    """Validate the configuration invariants shared by ``check`` and startup.

    This deliberately excludes host hardware and MediaPipe initialization.  The
    acknowledgement flag can be supplied on the command line at startup, while
    every invariant below is an unconditional V0.1 runtime requirement.
    """

    if not is_loopback_host(config.app.host):
        raise RuntimeBuildError(
            "the V0.1 FastAPI service must stay on a loopback host"
        )
    if not config.pipeline.perception_enabled:
        raise RuntimeBuildError("production mapping requires perception_enabled=true")
    if config.remote_inference.enabled:
        raise RuntimeBuildError(
            "remote inference is an experimental contract and is not active in "
            "V0.1; use the single-host Windows topology"
        )
    if not config.hand_tracking.enabled or not config.keyboard_tracking.enabled:
        raise RuntimeBuildError("hand and keyboard tracking must both be enabled")
    hand_model_path = config.hand_tracking.model_path or DEFAULT_MODEL_PATH
    if not hand_model_path.is_file():
        raise RuntimeBuildError(
            f"MediaPipe Hand Landmarker asset is missing: {hand_model_path}"
        )
    if not config.debug_ui.enabled:
        raise RuntimeBuildError(
            "the V0.1 single-host topology requires the local FastAPI service"
        )
    if not config.debug_ui.websocket_state:
        raise RuntimeBuildError(
            "the exact-frame debug inspector requires websocket_state=true"
        )


def _load_artifacts(config: DeskVisionConfig) -> KeyboardCalibrationArtifacts:
    try:
        return load_keyboard_calibration_artifacts(
            layout_path=config.artifacts.layout_profile,
            anchor_path=config.artifacts.anchor_reference,
            contact_map_path=config.artifacts.contact_map,
        )
    except Exception as exc:
        raise RuntimeBuildError(f"keyboard calibration artifacts are invalid: {exc}") from exc


def _model_files_match(
    generated: GeneratedKeyboardModel,
    *,
    model_path: Path,
    manifest_path: Path,
) -> bool:
    if not model_path.is_file() or not manifest_path.is_file():
        return False
    try:
        saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        saved_bytes = model_path.read_bytes()
    except (OSError, json.JSONDecodeError):
        return False
    expected_model = generated.manifest["model"]
    return bool(
        saved_manifest == generated.manifest
        and isinstance(expected_model, dict)
        and hashlib.sha256(saved_bytes).hexdigest() == expected_model.get("sha256")
    )


def ensure_keyboard_model(
    artifacts: KeyboardCalibrationArtifacts,
    *,
    model_path: Path,
    manifest_path: Path,
    repair: bool = True,
) -> GeneratedKeyboardModel:
    """Validate the generated model and optionally repair its output files."""

    if model_path.expanduser().resolve(strict=False) == manifest_path.expanduser().resolve(
        strict=False
    ):
        raise RuntimeBuildError("model GLB and manifest paths must be different")

    generated = generate_adaptive_keyboard_model(
        artifacts.layout,
        artifacts.contact_map,
    )
    matches = _model_files_match(
        generated,
        model_path=model_path,
        manifest_path=manifest_path,
    )
    if not matches and not repair:
        raise RuntimeBuildError(
            "adaptive keyboard GLB/manifest does not match the calibration bundle"
        )
    if not matches:
        # Publish bytes first and the manifest second. Readers never observe a
        # new manifest pointing at incomplete GLB bytes.
        atomic_write_bytes(model_path, generated.glb)
        atomic_write_json(manifest_path, generated.manifest)
    return generated


@dataclass(slots=True)
class DeskVisionRuntime:
    """Own camera, perception, latest state, and the optional debug app."""

    config: DeskVisionConfig
    artifacts: KeyboardCalibrationArtifacts
    source: FrameSource
    frames: LatestFrameStore
    capture: CaptureThread
    pipeline: ProductionMappingPipeline
    states: LatestSceneStateStore
    local_publisher: LocalWebSocketPublisher
    perception: LatestFramePerceptionWorker
    encoder: LatestJpegEncoder
    web_app: Any
    _started: bool = False
    _closed: bool = False
    _status: str = "created"

    def start(self) -> None:
        if self._started:
            if self._status == "running":
                return
            raise RuntimeError(
                f"runtime is {self._status}; complete shutdown before rebuilding"
            )
        if self._closed:
            raise RuntimeError("a stopped DeskVisionRuntime cannot be restarted")
        if self._status != "created":
            raise RuntimeError(
                f"runtime is {self._status}; complete shutdown and rebuild it"
            )
        try:
            self._status = "starting"
            self.capture.start()
            self.perception.start()
        except Exception as start_error:
            cleanup_errors: list[Exception] = []
            for close in (
                lambda: self.perception.stop(timeout_s=self.config.pipeline.stop_timeout_s),
                lambda: self.capture.stop(timeout_s=self.config.pipeline.stop_timeout_s),
            ):
                try:
                    close()
                except Exception as cleanup_error:
                    cleanup_errors.append(cleanup_error)
            if not self.perception.is_running:
                try:
                    self.pipeline.close()
                except Exception as cleanup_error:
                    cleanup_errors.append(cleanup_error)
            self._started = self.capture.is_running or self.perception.is_running
            self._closed = not self._started and not cleanup_errors
            self._status = "closed" if self._closed else "startup_cleanup_failed"
            if cleanup_errors:
                raise RuntimeError(
                    "runtime startup failed and cleanup was incomplete: "
                    + " | ".join(
                        f"{type(exc).__name__}: {exc}" for exc in cleanup_errors
                    )
                ) from start_error
            raise
        self._started = True
        self._status = "running"

    def stop(self) -> None:
        if self._closed:
            return
        self._status = "stopping"
        errors: list[Exception] = []
        try:
            self.capture.stop(timeout_s=self.config.pipeline.stop_timeout_s)
        except Exception as exc:
            errors.append(exc)
        try:
            self.perception.stop(timeout_s=self.config.pipeline.stop_timeout_s)
        except Exception as exc:
            errors.append(exc)
        # Closing a MediaPipe tracker while the perception thread is still
        # executing it is unsafe. A timeout keeps the runtime retryable.
        if not self.perception.is_running:
            try:
                self.pipeline.close()
            except Exception as exc:
                errors.append(exc)
        elif not errors:
            errors.append(RuntimeError("perception thread is still running"))

        components_running = self.capture.is_running or self.perception.is_running
        self._started = components_running
        self._closed = not components_running and not errors
        self._status = "closed" if self._closed else "shutdown_failed"
        if errors:
            raise RuntimeError(
                "runtime shutdown failed: "
                + " | ".join(f"{type(exc).__name__}: {exc}" for exc in errors)
            )

    @property
    def is_running(self) -> bool:
        # Do not hide a thread that survived a shutdown deadline.
        return self.capture.is_running or self.perception.is_running

    @property
    def status(self) -> str:
        return self._status

    def __enter__(self) -> "DeskVisionRuntime":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()


def build_runtime(
    config: DeskVisionConfig,
    *,
    source: FrameSource | None = None,
    hand_tracker: HandTracker | None = None,
    marker_detector: ArucoObservationDetector | None = None,
) -> DeskVisionRuntime:
    """Validate every startup dependency before camera threads are started."""

    if source is None:
        require_windows_runtime()
    validate_runtime_config(config)

    artifacts = _load_artifacts(config)
    generated = ensure_keyboard_model(
        artifacts,
        model_path=config.artifacts.model_glb,
        manifest_path=config.artifacts.model_manifest,
    )
    model_payload = generated.manifest.get("model")
    if not isinstance(model_payload, dict):
        raise RuntimeBuildError("generated keyboard manifest has no model metadata")

    owns_hand_tracker = hand_tracker is None
    if hand_tracker is None:
        hand_options: dict[str, object] = {
            "num_hands": config.hand_tracking.num_hands,
            "min_hand_detection_confidence": (
                config.hand_tracking.min_detection_confidence
            ),
            "min_hand_presence_confidence": (
                config.hand_tracking.min_presence_confidence
            ),
            "min_tracking_confidence": config.hand_tracking.min_tracking_confidence,
            "metrics_acknowledged": config.hand_tracking.metrics_acknowledged,
        }
        if config.hand_tracking.model_path is not None:
            hand_options["model_path"] = config.hand_tracking.model_path
        hand_tracker = MediaPipeHandTracker(
            MediaPipeHandTrackerConfig(**hand_options)
        )

    try:
        locator = ArucoKeyboardLocator(
            artifacts.anchor_reference,
            detector=marker_detector,
            config=ArucoKeyboardLocatorConfig(
                max_coast_ms=config.keyboard_tracking.max_coast_ms,
                max_frame_age_ms=config.diagnostics.stale_frame_threshold_ms,
                jitter_alpha=min(config.keyboard_tracking.smoothing_alpha, 0.72),
            ),
        )
        mapper = ContactKeyMapper(
            artifacts.contact_map,
            ContactKeyMapperConfig(
                top_n=config.keyboard_tracking.top_k,
                min_landmark_confidence=config.keyboard_tracking.min_hand_confidence,
                min_pose_confidence=config.keyboard_tracking.min_pose_confidence,
                direct_spatial_weight=config.keyboard_tracking.direct_weight,
                direct_probability=config.interaction.direct_min_intensity,
                # WindowsCameraSource deliberately preserves raw sensor
                # orientation. Browser preview mirroring is a separate view.
                source_coordinates_mirrored=False,
            ),
        )
        model_state = KeyboardModelState(
            revision=str(generated.manifest["model_revision"]),
            sha256=str(model_payload["sha256"]),
            uri="/api/model/keyboard.glb",
            manifest_uri="/api/model/manifest",
            key_count=len(artifacts.layout.keys),
        )
        frame_store = LatestFrameStore()
        state_store = LatestSceneStateStore()
        camera_source = source or WindowsCameraSource(config.camera)
        capture = CaptureThread(
            camera_source,
            frame_store,
            stale_frame_threshold_ms=config.diagnostics.stale_frame_threshold_ms,
        )
        pipeline = ProductionMappingPipeline(
            hand_tracker=hand_tracker,
            keyboard_locator=locator,
            key_mapper=mapper,
            artifact_revisions=ArtifactRevisions(
                layout_content=artifacts.layout.content_hash,
                layout_inventory=artifacts.inventory.revision,
                anchor=artifacts.anchor_reference.revision,
                contact_map=artifacts.contact_map.revision,
                model=model_state.revision,
            ),
            model=model_state,
            capture_metrics=capture.metrics,
            config=MappingPipelineConfig(
                neighbor_glow_enabled=config.interaction.neighbor_glow_enabled,
                neighbor_glow_scale=config.interaction.neighbor_glow_scale,
                direct_min_intensity=config.interaction.direct_min_intensity,
            ),
        )
        local_publisher = LocalWebSocketPublisher(state_store)
        worker = LatestFramePerceptionWorker(
            frame_store,
            pipeline,
            state_store,
            publishers=(local_publisher,),
            frame_wait_timeout_s=config.pipeline.frame_wait_timeout_ms / 1000.0,
            stale_after_ms=config.diagnostics.stale_frame_threshold_ms,
        )
        encoder = LatestJpegEncoder(quality=config.stream.jpeg_quality)
        context = DebugWebContext(
            frames=frame_store,
            states=state_store,
            encoder=encoder,
            capture_metrics=capture.metrics,
            perception_metrics=worker.stats,
            layout_path=config.artifacts.layout_profile,
            model_path=config.artifacts.model_glb,
            manifest_path=config.artifacts.model_manifest,
            mirror_preview=config.debug_ui.mirror_preview,
            max_preview_fps=config.stream.max_preview_fps,
            expose_model_download=config.debug_ui.expose_model_download,
            bundle_stale_after_ms=config.diagnostics.stale_frame_threshold_ms,
            health_interval_ms=config.diagnostics.metrics_interval_ms,
            layout_snapshot=artifacts.layout.to_dict(),
            model_snapshot=generated.glb,
            manifest_snapshot=generated.manifest,
        )
        return DeskVisionRuntime(
            config=config,
            artifacts=artifacts,
            source=camera_source,
            frames=frame_store,
            capture=capture,
            pipeline=pipeline,
            states=state_store,
            local_publisher=local_publisher,
            perception=worker,
            encoder=encoder,
            web_app=create_debug_app(context),
        )
    except Exception:
        if owns_hand_tracker:
            hand_tracker.close()
        raise


__all__ = [
    "DeskVisionRuntime",
    "RuntimeBuildError",
    "build_runtime",
    "ensure_keyboard_model",
    "validate_runtime_config",
]
