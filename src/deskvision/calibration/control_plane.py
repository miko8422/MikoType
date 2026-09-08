"""Explicit setup-mode orchestration for user layout and contact calibration."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
import time

import cv2
import numpy as np

from deskvision.calibration.anchor_reference import (
    AnchorReference,
    load_anchor_reference,
    marker_key_ids_from_profile,
    save_anchor_reference,
)
from deskvision.calibration._storage import atomic_write_json
from deskvision.calibration.anchor_registration import AnchorRegistrationAccumulator
from deskvision.calibration.contact_map import (
    LayoutInventory,
    build_layout_inventory,
)
from deskvision.calibration.layout_profile import (
    KeyboardLayoutProfile,
    load_layout_profile,
    save_layout_profile,
)
from deskvision.calibration.session import ContactCalibrationSession
from deskvision.core.config import ArtifactConfig, CameraConfig
from deskvision.keyboard.bundle import KeyboardBundlePaths, build_keyboard_bundle
from deskvision.perception.aruco_keyboard import ArucoKeyboardLocator, OpenCVArucoDetector
from deskvision.perception.hand_base import DetectedHand
from deskvision.state.store import LatestSceneStateStore
from deskvision.video.latest_frame import LatestFrameStore


class SetupUnavailableError(RuntimeError):
    """The current setup step cannot consume a safe same-frame observation."""


@dataclass(frozen=True, slots=True)
class SetupWorkspace:
    root: Path

    @property
    def layout(self) -> Path:
        return self.root / "layout.json"

    @property
    def anchor(self) -> Path:
        return self.root / "anchor_reference.json"

    @property
    def draft(self) -> Path:
        return self.root / "contact_calibration_draft.json"

    @property
    def contact_map(self) -> Path:
        return self.root / "contact_map.json"


class KeyboardSetupController:
    """Run setup actions only when explicitly selected by the operator."""

    def __init__(
        self,
        *,
        active_artifacts: ArtifactConfig,
        camera_config: CameraConfig,
        frames: LatestFrameStore,
        states: LatestSceneStateStore,
        workspace: SetupWorkspace,
    ) -> None:
        self.active_artifacts = active_artifacts
        self.camera_config = camera_config
        self.frames = frames
        self.states = states
        self.workspace = workspace
        self._validate_workspace_isolation()
        self._lock = Lock()
        self._detector = OpenCVArucoDetector()
        self._registration: AnchorRegistrationAccumulator | None = None
        self._contact_session: ContactCalibrationSession | None = None
        self._contact_locator: ArucoKeyboardLocator | None = None

    @property
    def camera_revalidation_path(self) -> Path:
        return self.workspace.root / "camera_revalidation_required.json"

    def _bind_staging_camera(self) -> None:
        """Catch camera/config changes across restarts, not just live selection."""
        path = self.workspace.root / "camera_binding.json"
        identity = {name: getattr(self.camera_config, name) for name in (
            "device_index", "backend", "width", "height", "rotate_degrees", "mirror"
        )}
        if path.is_file():
            previous = json.loads(path.read_text(encoding="utf-8"))
            if previous == identity:
                return
            atomic_write_json(self.camera_revalidation_path, {
                "reason": "camera configuration changed; finalize new marker anchors",
                "camera": identity,
            })
            self._registration = None
            self._contact_session = None
            self._contact_locator = None
        atomic_write_json(path, identity)

    @contextmanager
    def camera_change(self, camera: CameraConfig):
        """Serialize hardware changes with sampling; retain old files for review."""
        with self._lock:
            self._bind_staging_camera()
            changed = (
                camera.device_index != self.camera_config.device_index
                or camera.backend != self.camera_config.backend
            )
            if changed:
                atomic_write_json(self.camera_revalidation_path, {
                    "reason": "camera changed; register and finalize markers again",
                    "device_index": camera.device_index,
                    "backend": camera.backend,
                })
                self._registration = None
                self._contact_session = None
                self._contact_locator = None
            self.camera_config = camera
            self._bind_staging_camera()
            yield

    def _require_camera_revalidation(self) -> None:
        self._bind_staging_camera()
        if self.camera_revalidation_path.exists():
            raise SetupUnavailableError(
                "camera changed: register and finalize marker anchors again before "
                "contact sampling or applying a keyboard bundle"
            )

    def _validate_workspace_isolation(self) -> None:
        staging = {
            "layout": self.workspace.layout,
            "anchor": self.workspace.anchor,
            "contact draft": self.workspace.draft,
            "contact map": self.workspace.contact_map,
        }
        active = {
            name: getattr(self.active_artifacts, name)
            for name in self.active_artifacts.__dataclass_fields__
        }
        active_paths = {
            path.expanduser().resolve(strict=False): name
            for name, path in active.items()
        }
        for staging_role, path in staging.items():
            canonical = path.expanduser().resolve(strict=False)
            active_role = active_paths.get(canonical)
            if active_role is not None:
                raise ValueError(
                    "keyboard setup workspace must be separate from production "
                    f"artifacts: staging {staging_role} overlaps {active_role} at "
                    f"{canonical}"
                )

    def _ensure_staging_layout(self) -> None:
        """Create setup state lazily so normal runtime does not depend on it."""

        self.workspace.root.mkdir(parents=True, exist_ok=True)
        self._bind_staging_camera()
        if not self.workspace.layout.exists():
            active = load_layout_profile(self.active_artifacts.layout_profile)
            save_layout_profile(self.workspace.layout, active)

    def layout_state(self) -> dict[str, object]:
        with self._lock:
            self._ensure_staging_layout()
            profile = load_layout_profile(self.workspace.layout)
        return {
            "schema_version": "keyboard-setup-layout-state-0.1",
            "profile": profile.to_dict(),
            "content_hash": profile.content_hash,
            "inventory_revision": profile.inventory_revision,
            "path": str(self.workspace.layout),
        }

    def save_layout(self, payload: Mapping[str, object]) -> dict[str, object]:
        with self._lock:
            self._ensure_staging_layout()
            persisted = save_layout_profile(self.workspace.layout, payload)
            self._registration = None
            self._contact_session = None
            self._contact_locator = None
        return {
            "saved": True,
            "profile": persisted.to_dict(),
            "content_hash": persisted.content_hash,
            "inventory_revision": persisted.inventory_revision,
            "downstream_status": "anchor_and_contact_must_be_revalidated",
        }

    def start_anchor_registration(self, *, reset: bool = False) -> dict[str, object]:
        with self._lock:
            self._ensure_staging_layout()
            if self._registration is None or reset:
                profile = load_layout_profile(self.workspace.layout)
                assignments = marker_key_ids_from_profile(profile)
                registration = AnchorRegistrationAccumulator(
                    assignments,
                    reference_marker_id=min(assignments),
                )
                # A new registration starts a new staging lineage.  Keeping any
                # previously finalized files here would allow Apply to publish a
                # stale anchor/contact pair while the replacement is incomplete.
                self._invalidate_staging_anchor_lineage()
                self._registration = registration
            return self._registration.snapshot(timestamp_ms=time.time() * 1000.0)

    def observe_anchors(self) -> dict[str, object]:
        with self._lock:
            registration = self._registration
            if registration is None:
                raise SetupUnavailableError("anchor registration has not been started")
            frame = self.frames.latest()
            if frame is None:
                raise SetupUnavailableError("no camera frame is available")
            observations = self._detector.detect(frame)
            contributed = registration.observe(
                observations,
                source_id=frame.source_id,
                frame_id=frame.frame_id,
                timestamp_ms=frame.acquired_at_ns / 1_000_000.0,
            )
            progress = registration.snapshot(
                timestamp_ms=frame.acquired_at_ns / 1_000_000.0
            )
            return {
                **progress,
                "frame_id": frame.frame_id,
                "detected_marker_ids": sorted(observations),
                "frame_contributed": contributed,
            }

    def finalize_anchor(self) -> dict[str, object]:
        with self._lock:
            if self._registration is None:
                raise SetupUnavailableError("anchor registration has not been started")
            reference = self._registration.build_reference()
            # Contact samples are expressed in the previous anchor coordinate
            # system and must never survive a newly finalized reference.
            self.workspace.draft.unlink(missing_ok=True)
            self.workspace.contact_map.unlink(missing_ok=True)
            save_anchor_reference(self.workspace.anchor, reference)
            self.camera_revalidation_path.unlink(missing_ok=True)
            self._contact_session = None
            self._contact_locator = None
            return {
                "saved": True,
                "reference": reference.to_dict(),
                "path": str(self.workspace.anchor),
            }

    def start_contact_calibration(self) -> dict[str, object]:
        with self._lock:
            self._require_camera_revalidation()
            profile, inventory, reference = self._load_staging_contact_inputs()
            del profile
            self._contact_session = ContactCalibrationSession.resume_or_new(
                inventory,
                reference,
                draft_path=self.workspace.draft,
                final_path=self.workspace.contact_map,
            )
            self._contact_locator = ArucoKeyboardLocator(reference)
            return self._contact_session.to_dict()

    def contact_state(self) -> dict[str, object]:
        with self._lock:
            if self._contact_session is None:
                return {
                    "schema_version": "keyboard-contact-calibration-session-0.1",
                    "status": "not_started",
                }
            return self._contact_session.to_dict()

    def capture_contact(self, *, key_id: str | None = None) -> dict[str, object]:
        with self._lock:
            session = self._contact_session
            locator = self._contact_locator
            if session is None or locator is None:
                raise SetupUnavailableError("contact calibration has not been started")
            frame, state = self._latest_same_frame_pair()
            if frame is None or state is None:
                raise SetupUnavailableError("same-frame camera and hand state are unavailable")
            pose = locator.locate(frame)
            if not pose.usable or pose.image_to_reference is None:
                raise SetupUnavailableError(
                    f"keyboard markers are not usable: {pose.reason or pose.status}"
                )
            hand = self._physical_right_hand(state.hands)
            if hand is None:
                raise SetupUnavailableError("physical right hand is not visible")
            landmark = hand.landmarks[8]
            image_point = np.asarray(
                [[[landmark.x * frame.width, landmark.y * frame.height]]],
                dtype=np.float64,
            )
            reference = cv2.perspectiveTransform(
                image_point,
                np.asarray(pose.image_to_reference, dtype=np.float64),
            )[0, 0]
            result = session.capture_reference_point(
                (float(reference[0]), float(reference[1])),
                key_id=key_id,
            )
            return {
                "capture": result.to_dict(),
                "session": session.to_dict(),
                "frame_id": frame.frame_id,
                "pose": pose.to_dict(),
            }

    def _latest_same_frame_pair(self):
        """Read the worker's atomic processed frame/state pair.

        Capture may already be several frames ahead; calibration intentionally
        uses the newest *processed* bundle instead of trying to join two
        independent latest stores at request time.
        """

        bundle = self.states.bundles.latest()
        if bundle is None:
            return None, None
        return bundle.frame, bundle.state

    def undo_contact(self) -> dict[str, object]:
        with self._lock:
            if self._contact_session is None:
                raise SetupUnavailableError("contact calibration has not been started")
            result = self._contact_session.undo_last()
            return {"capture": result.to_dict(), "session": self._contact_session.to_dict()}

    def pause_contact(self, paused: bool) -> dict[str, object]:
        with self._lock:
            if self._contact_session is None:
                raise SetupUnavailableError("contact calibration has not been started")
            return (
                self._contact_session.pause()
                if paused
                else self._contact_session.resume()
            )

    def reset_contact_draft(self) -> dict[str, object]:
        with self._lock:
            self.workspace.draft.unlink(missing_ok=True)
            self.workspace.contact_map.unlink(missing_ok=True)
            self._contact_session = None
            self._contact_locator = None
        return {"reset": True}

    def apply_bundle(self) -> dict[str, object]:
        with self._lock:
            self._require_camera_revalidation()
            self._ensure_staging_layout()
            if not self.workspace.anchor.is_file() or not self.workspace.contact_map.is_file():
                raise SetupUnavailableError(
                    "finalized anchor and Contact Map are required before apply"
                )
            result = build_keyboard_bundle(
                source_layout=self.workspace.layout,
                source_anchor=self.workspace.anchor,
                source_contact_map=self.workspace.contact_map,
                destination=KeyboardBundlePaths(
                    layout=self.active_artifacts.layout_profile,
                    anchor=self.active_artifacts.anchor_reference,
                    contact_map=self.active_artifacts.contact_map,
                    model=self.active_artifacts.model_glb,
                    manifest=self.active_artifacts.model_manifest,
                ),
            )
            return {
                "applied": True,
                "restart_required": True,
                "key_count": result.key_count,
                "model_revision": result.model_revision,
                "model_sha256": result.model_sha256,
            }

    def marker_png(self, marker_id: int, *, size_px: int = 512) -> bytes:
        if not 0 <= marker_id < 50:
            raise ValueError("DICT_4X4_50 marker_id must be between 0 and 49")
        if not 64 <= size_px <= 2048:
            raise ValueError("marker size must be between 64 and 2048 pixels")
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        image = cv2.aruco.generateImageMarker(dictionary, marker_id, size_px)
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise RuntimeError("OpenCV failed to encode the marker")
        return encoded.tobytes()

    def _load_staging_contact_inputs(
        self,
    ) -> tuple[KeyboardLayoutProfile, LayoutInventory, AnchorReference]:
        self._ensure_staging_layout()
        profile = load_layout_profile(self.workspace.layout)
        inventory = build_layout_inventory(profile)
        if not self.workspace.anchor.is_file():
            raise SetupUnavailableError("finalize an anchor reference first")
        reference = load_anchor_reference(self.workspace.anchor, inventory=inventory)
        return profile, inventory, reference

    def _invalidate_staging_anchor_lineage(self) -> None:
        """Discard artifacts whose coordinate system belongs to an old anchor."""

        self.workspace.anchor.unlink(missing_ok=True)
        self.workspace.draft.unlink(missing_ok=True)
        self.workspace.contact_map.unlink(missing_ok=True)
        self._contact_session = None
        self._contact_locator = None

    def _physical_right_hand(
        self,
        hands: tuple[DetectedHand | Mapping[str, object], ...],
    ) -> DetectedHand | None:
        for hand in hands:
            if not isinstance(hand, DetectedHand):
                continue
            raw = hand.handedness
            # The production camera source is never mirrored before MediaPipe;
            # UI mirroring does not alter algorithm coordinates.
            physical = {"left": "right", "right": "left"}.get(raw, raw)
            if physical == "right":
                return hand
        return None


__all__ = [
    "KeyboardSetupController",
    "SetupUnavailableError",
    "SetupWorkspace",
]
