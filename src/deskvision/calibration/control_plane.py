"""Explicit setup-mode orchestration for user layout and contact calibration."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
import hashlib
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
from deskvision.calibration._storage import atomic_write_json, canonical_hash
from deskvision.calibration.anchor_registration import AnchorRegistrationAccumulator
from deskvision.calibration.contact_map import (
    LayoutInventory,
    build_layout_inventory,
    load_calibration_draft,
    load_contact_map,
)
from deskvision.calibration.layout_profile import (
    KeyboardLayoutProfile,
    load_layout_profile,
    save_layout_profile,
)
from deskvision.calibration.session import ContactCalibrationSession
from deskvision.core.config import ArtifactConfig, CameraConfig
from deskvision.keyboard.bundle import KeyboardBundlePaths, build_keyboard_bundle
from deskvision.keyboard.adaptive_model import layout_geometry_revision
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
        self._restart_required = False
        self._runtime_mapping_block: str | None = None
        if self.runtime_camera_revalidation_path.is_file() or self._camera_revalidation_pending():
            self._runtime_mapping_block = "camera_changed_recalibration_required"

    @property
    def camera_revalidation_path(self) -> Path:
        return self.workspace.root / "camera_revalidation_required.json"

    @property
    def runtime_camera_revalidation_path(self) -> Path:
        return self.workspace.root / "runtime_camera_revalidation_required.json"

    @property
    def staging_lineage_path(self) -> Path:
        return self.workspace.root / "replacement_lineage.json"

    @staticmethod
    def _camera_identity(camera: CameraConfig) -> dict[str, object]:
        return {name: getattr(camera, name, False) for name in (
            "device_index", "backend", "width", "height", "rotate_degrees", "mirror",
            "flip_vertical",
        )}

    def _camera_revalidation_pending(self) -> bool:
        """Read-only counterpart to binding; safe for dashboard polling."""
        if self.camera_revalidation_path.is_file():
            return True
        path = self.workspace.root / "camera_binding.json"
        if not path.is_file():
            # Without a saved baseline, imported calibration cannot establish
            # that reflected/rotated input matches the camera it was built for.
            return bool(self.camera_config.mirror or self.camera_config.flip_vertical or self.camera_config.rotate_degrees)
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(previous, dict):
                return True
            # Older camera bindings predate vertical pixel correction.
            previous.setdefault("flip_vertical", False)
            return previous != self._camera_identity(self.camera_config)
        except (OSError, ValueError):
            return True

    def runtime_mapping_block_reason(self) -> str | None:
        """Worker callback: never acquire the setup lock or perform disk I/O.

        A camera change holds that lock while joining the processing worker.
        The cached value also keeps the old in-memory mapper blocked after Apply
        until the process reloads the newly generated calibration bundle.
        """
        return self._runtime_mapping_block

    def _mark_camera_revalidation(self, camera: CameraConfig) -> None:
        self._runtime_mapping_block = "camera_changed_recalibration_required"
        payload = {
            "reason": "camera configuration changed; finalize new marker anchors and apply the recalibrated bundle",
            "camera": self._camera_identity(camera),
        }
        atomic_write_json(self.camera_revalidation_path, payload)
        atomic_write_json(self.runtime_camera_revalidation_path, payload)
        self._registration = None
        self._contact_session = None
        self._contact_locator = None

    def _bind_staging_camera(self) -> None:
        """Catch camera/config changes across restarts, not just live selection."""
        path = self.workspace.root / "camera_binding.json"
        identity = self._camera_identity(self.camera_config)
        if path.is_file():
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A corrupt baseline is unknown, never equivalent to the new
                # camera. Persist the block before replacing the broken file.
                previous = None
            if isinstance(previous, dict):
                previous.setdefault("flip_vertical", False)
            if previous == identity:
                return
            self._mark_camera_revalidation(self.camera_config)
        elif self._camera_revalidation_pending():
            self._mark_camera_revalidation(self.camera_config)
        atomic_write_json(path, identity)

    def bind_runtime_camera(self) -> dict[str, object]:
        """Establish a startup baseline, explicitly outside read-only GET paths.

        Normal settings saves can change input configuration for the next
        process. Recording the *running* camera here lets that next process
        detect the change even when the operator never opens keyboard setup.
        Only camera metadata is written; calibration and layout files are not.
        """
        with self._lock:
            try:
                self._bind_staging_camera()
                if self._runtime_mapping_block and not self.runtime_camera_revalidation_path.is_file():
                    self._mark_camera_revalidation(self.camera_config)
            except OSError as exc:
                # Keep raw camera/UI diagnostics available when staging is
                # unwritable, but never use a mapper without a durable baseline.
                self._runtime_mapping_block = "camera_binding_unavailable"
                return {"bound": False, "mapping_blocked": True, "error": str(exc)}
            return {"bound": True, "mapping_blocked": self._runtime_mapping_block is not None}

    @contextmanager
    def camera_change(self, camera: CameraConfig):
        """Serialize hardware changes with sampling; retain old files for review."""
        with self._lock:
            self._bind_staging_camera()
            changed = self._camera_identity(camera) != self._camera_identity(self.camera_config)
            if changed:
                self._mark_camera_revalidation(camera)
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
            atomic_write_json(self.workspace.layout, active.to_dict())

    @staticmethod
    def _layout_configuration_revision(profile: KeyboardLayoutProfile) -> str:
        """Ignore save timestamps when deciding whether calibration can be reused."""
        payload = profile.to_dict()
        payload.pop("created_at", None)
        payload.pop("updated_at", None)
        return canonical_hash(payload)

    def layout_state(self) -> dict[str, object]:
        with self._lock:
            path = self.workspace.layout if self.workspace.layout.is_file() else self.active_artifacts.layout_profile
            profile = load_layout_profile(path)
        return {
            "schema_version": "keyboard-setup-layout-state-0.1",
            "profile": profile.to_dict(),
            "content_hash": profile.content_hash,
            "inventory_revision": profile.inventory_revision,
            "path": str(path),
            "source": "staging" if path == self.workspace.layout else "active",
        }

    def _artifact_summary(
        self, *, layout_path: Path, anchor_path: Path, contact_path: Path,
        warnings: list[str], label: str, draft_path: Path | None = None,
        model_path: Path | None = None, manifest_path: Path | None = None,
    ) -> dict[str, object]:
        """Report existing files, validating lineage without creating sessions."""
        result: dict[str, object] = {}
        profile = inventory = reference = contact = None
        if layout_path.is_file():
            try:
                profile = load_layout_profile(layout_path)
                inventory = build_layout_inventory(profile)
                result["layout"] = {
                    "layout_id": profile.layout_id, "key_count": len(profile.keys),
                    "content_hash": profile.content_hash,
                    "configuration_revision": self._layout_configuration_revision(profile),
                    "inventory_revision": profile.inventory_revision, "valid": True,
                }
            except (OSError, ValueError) as exc:
                result["layout"] = {"valid": False}
                warnings.append(f"{label}键位清单不可用：{exc}")
        if anchor_path.is_file():
            try:
                reference = load_anchor_reference(anchor_path, inventory=inventory)
                valid = profile is not None
                if profile is not None:
                    planned = marker_key_ids_from_profile(profile)
                    valid = all(planned.get(item.marker_id) == item.key_id for item in reference.anchors)
                result["anchor"] = {
                    "revision": reference.revision, "marker_count": len(reference.anchors),
                    "reference_marker_id": reference.reference_marker_id,
                    "markers": [{"marker_id": item.marker_id, "key_id": item.key_id} for item in reference.anchors],
                    "valid": valid,
                }
                if not valid:
                    warnings.append(f"{label}Anchor 与当前键位清单不兼容，需要重新注册。")
            except (OSError, ValueError) as exc:
                result["anchor"] = {"valid": False}
                warnings.append(f"{label}Anchor 不可用：{exc}")
        if contact_path.is_file():
            try:
                contact = load_contact_map(contact_path)
                valid = inventory is not None and reference is not None and bool(result.get("anchor", {}).get("valid"))
                if valid:
                    try:
                        contact.validate_revisions(inventory, reference.revision)
                    except ValueError:
                        valid = False
                samples = sum(len(key.samples) for key in contact.keys)
                result["contact"] = {
                    "revision": contact.revision, "status": "complete",
                    "completed_keys": len(contact.keys), "total_keys": len(contact.keys),
                    "captured_samples": samples, "total_samples": len(contact.keys) * contact.samples_per_key,
                    "valid": valid, "resume_available": False,
                }
                if not valid:
                    warnings.append(f"{label}触点校准与当前清单或 Anchor 不匹配，不可应用。")
            except (OSError, ValueError) as exc:
                result["contact"] = {"valid": False}
                warnings.append(f"{label}触点校准不可用：{exc}")
        elif draft_path is not None and draft_path.is_file():
            try:
                if inventory is None or reference is None or not result.get("anchor", {}).get("valid"):
                    raise ValueError("draft requires a compatible layout and Anchor")
                draft = load_calibration_draft(draft_path, inventory, reference.revision)
                result["contact"] = {
                    "status": "draft_complete" if draft.complete else "collecting",
                    "completed_keys": sum(len(values) == draft.samples_per_key for values in draft.samples_by_key.values()),
                    "total_keys": len(inventory.keys),
                    "captured_samples": sum(len(values) for values in draft.samples_by_key.values()),
                    "total_samples": len(inventory.keys) * draft.samples_per_key,
                    "valid": True, "resume_available": True,
                }
            except (OSError, ValueError) as exc:
                result["contact"] = {"status": "invalid_draft", "valid": False, "resume_available": False}
                warnings.append(f"{label}触点草稿不可用：{exc}")
        if model_path is not None and manifest_path is not None and (model_path.is_file() or manifest_path.is_file()):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                model = manifest["model"]
                model_bytes = model_path.read_bytes()
                valid = bool(
                    profile is not None and contact is not None
                    and result.get("contact", {}).get("valid")
                    and manifest["layout"]["inventory_revision"] == profile.inventory_revision
                    and manifest["geometry_revision"] == layout_geometry_revision(profile)
                    and manifest["contact_binding"]["contact_map_revision"] == contact.revision
                    and manifest["contact_binding"]["anchor_revision"] == contact.anchor_revision
                    and len(model_bytes) == model["byte_length"]
                    and hashlib.sha256(model_bytes).hexdigest() == model["sha256"]
                )
                result["model"] = {"revision": manifest["model_revision"], "key_count": manifest["layout"]["key_count"], "valid": valid}
                if not valid:
                    warnings.append(f"{label}3D 模型与校准数据不匹配，需要重新应用生成。")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                result["model"] = {"valid": False}
                warnings.append(f"{label}3D 模型不可用：{exc}")
        required = ("layout", "anchor", "contact", "model") if model_path is not None else ("layout", "anchor", "contact")
        result["valid"] = all(result.get(name, {}).get("valid") for name in required) and result.get("contact", {}).get("status") == "complete"
        return result

    def overview_state(self) -> dict[str, object]:
        """Pure read: dashboard/tutorial navigation never initializes staging."""
        with self._lock:
            warnings: list[str] = []
            active = self._artifact_summary(
                layout_path=self.active_artifacts.layout_profile,
                anchor_path=self.active_artifacts.anchor_reference,
                contact_path=self.active_artifacts.contact_map,
                model_path=self.active_artifacts.model_glb,
                manifest_path=self.active_artifacts.model_manifest,
                warnings=warnings, label="已应用",
            )
            active["verification"] = "artifact_contract_only"
            staging = self._artifact_summary(
                layout_path=self.workspace.layout, anchor_path=self.workspace.anchor,
                contact_path=self.workspace.contact_map, draft_path=self.workspace.draft,
                warnings=warnings, label="待应用",
            )
            changed_layout = bool(staging.get("layout")) and staging.get("layout", {}).get("configuration_revision") != active.get("layout", {}).get("configuration_revision")
            replacement = changed_layout or self.staging_lineage_path.is_file() or any(name in staging for name in ("anchor", "contact"))
            staging_matches_active = bool(staging["valid"]) and not changed_layout and all(
                staging.get(name, {}).get("revision") == active.get(name, {}).get("revision")
                for name in ("anchor", "contact")
            )
            effective = staging if replacement else active
            camera_pending = self._camera_revalidation_pending()
            layout_ready = bool((staging.get("layout") or active.get("layout", {})).get("valid"))
            anchor_ready = bool(effective.get("anchor", {}).get("valid")) and not camera_pending
            contact_ready = anchor_ready and bool(effective.get("contact", {}).get("valid")) and effective.get("contact", {}).get("status") == "complete"
            if camera_pending:
                warnings.append("摄像头输入已改变：请重新锁定 Anchor、校准触点并应用。已保存的数据保留，但不能继续用于当前输入。")
            if self._runtime_mapping_block:
                warnings.append("运行中的旧键盘映射已停用；完成重新校准并应用后，请重启主服务。手部原始追踪仍可观测。")
            if self._restart_required:
                warnings.append("新键盘文件已应用；请重启主服务以加载新模型和映射。")
            registration = self._registration.snapshot(timestamp_ms=time.time() * 1000.0) if self._registration else None
            return {
                "schema_version": "mikotype-setup-status-0.1",
                "active": active, "staging": staging,
                "registration": registration,
                "camera_revalidation_required": camera_pending,
                "runtime_mapping_blocked": self._runtime_mapping_block is not None,
                "restart_required": self._restart_required,
                "steps": {
                    "layout": {"available": True, "complete": layout_ready},
                    "anchor": {"available": layout_ready, "complete": anchor_ready},
                    "contact": {"available": anchor_ready, "complete": contact_ready},
                    "apply": {"available": bool(staging["valid"]) and not camera_pending, "complete": bool(active["valid"]) and not camera_pending and (not replacement or staging_matches_active)},
                },
                "warnings": warnings,
            }

    def save_layout(self, payload: Mapping[str, object]) -> dict[str, object]:
        with self._lock:
            self._ensure_staging_layout()
            previous = load_layout_profile(self.workspace.layout)
            persisted = save_layout_profile(self.workspace.layout, payload)
            changed = self._layout_configuration_revision(previous) != self._layout_configuration_revision(persisted)
            if changed:
                atomic_write_json(self.staging_lineage_path, {"reason": "layout_changed"})
                self._invalidate_staging_anchor_lineage()
                self._registration = None
        return {
            "saved": True,
            "profile": persisted.to_dict(),
            "content_hash": persisted.content_hash,
            "inventory_revision": persisted.inventory_revision,
            "downstream_status": "anchor_and_contact_must_be_revalidated" if changed else "unchanged",
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
                atomic_write_json(self.staging_lineage_path, {"reason": "anchor_registration_started"})
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
            if not self._contact_session.complete:
                # An older finalized staging file must not be applied while its
                # explicitly started replacement is still collecting samples.
                self.workspace.contact_map.unlink(missing_ok=True)
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
            self._restart_required = True
            self.runtime_camera_revalidation_path.unlink(missing_ok=True)
            self.staging_lineage_path.unlink(missing_ok=True)
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
            # Explicitly starting contact calibration may reuse a compatible
            # active anchor. Merely viewing/skipping tutorial steps never copies
            # files or starts a new registration/session.
            active_profile = load_layout_profile(self.active_artifacts.layout_profile)
            if self.staging_lineage_path.is_file() or self._layout_configuration_revision(profile) != self._layout_configuration_revision(active_profile):
                raise SetupUnavailableError("finalize an anchor reference first")
            reference = load_anchor_reference(self.active_artifacts.anchor_reference, inventory=inventory)
            planned = marker_key_ids_from_profile(profile)
            if any(planned.get(anchor.marker_id) != anchor.key_id for anchor in reference.anchors):
                raise SetupUnavailableError("active anchors do not match the current layout")
            save_anchor_reference(self.workspace.anchor, reference)
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
            # A single input reflection changes the handedness convention;
            # display-only preview flips never alter algorithm coordinates.
            input_reflected = self.camera_config.mirror != getattr(self.camera_config, "flip_vertical", False)
            physical = raw if input_reflected else {"left": "right", "right": "left"}.get(raw, raw)
            if physical == "right":
                return hand
        return None


__all__ = [
    "KeyboardSetupController",
    "SetupUnavailableError",
    "SetupWorkspace",
]
