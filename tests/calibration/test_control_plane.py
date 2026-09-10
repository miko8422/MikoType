from pathlib import Path
from dataclasses import replace
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest

from deskvision.calibration import (
    load_anchor_reference,
    load_contact_map,
    save_anchor_reference,
    save_contact_map,
)
from deskvision.calibration.control_plane import (
    KeyboardSetupController,
    SetupUnavailableError,
    SetupWorkspace,
)
from deskvision.core.config import ArtifactConfig, CameraConfig
from deskvision.core.models import FramePacket
from deskvision.keyboard.bundle import KeyboardBundlePaths, build_keyboard_bundle
from deskvision.perception.hand_base import DetectedHand, HandLandmark
from deskvision.state.store import LatestSceneStateStore
from deskvision.state.scene_state import SCENE_STATE_SCHEMA_VERSION, SceneState
from deskvision.video.latest_frame import LatestFrameStore
from deskvision.web.setup import create_setup_router


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/keyboards/kzzi_user_adjustable_82"
pytestmark = pytest.mark.unit


def _active_bundle(tmp_path: Path) -> ArtifactConfig:
    target = tmp_path / "active"
    paths = KeyboardBundlePaths(
        target / "layout.json",
        target / "anchor_reference.json",
        target / "contact_map.json",
        target / "keyboard.glb",
        target / "manifest.json",
    )
    build_keyboard_bundle(
        source_layout=SOURCE / "layout.json",
        source_anchor=SOURCE / "anchor_reference.json",
        source_contact_map=SOURCE / "contact_map.json",
        destination=paths,
    )
    return ArtifactConfig(
        layout_profile=paths.layout,
        anchor_reference=paths.anchor,
        contact_map=paths.contact_map,
        model_glb=paths.model,
        model_manifest=paths.manifest,
    )


def _controller(tmp_path: Path) -> KeyboardSetupController:
    return KeyboardSetupController(
        active_artifacts=_active_bundle(tmp_path),
        camera_config=CameraConfig(),
        frames=LatestFrameStore(),
        states=LatestSceneStateStore(),
        workspace=SetupWorkspace(tmp_path / "staging"),
    )


def _seed_stale_staging_lineage(controller: KeyboardSetupController) -> None:
    # Fixtures deliberately create staging; dashboard GETs no longer do this.
    controller.save_layout(controller.layout_state()["profile"])
    save_anchor_reference(
        controller.workspace.anchor,
        load_anchor_reference(controller.active_artifacts.anchor_reference),
    )
    save_contact_map(
        controller.workspace.contact_map,
        load_contact_map(controller.active_artifacts.contact_map),
    )
    controller.workspace.draft.write_text("stale draft", encoding="utf-8")


def test_camera_change_preserves_but_blocks_old_staging_until_revalidated(tmp_path):
    controller = _controller(tmp_path)
    controller.layout_state()
    _seed_stale_staging_lineage(controller)
    before = controller.workspace.contact_map.read_bytes()
    with controller.camera_change(replace(controller.camera_config, device_index=1)):
        assert controller.camera_revalidation_path.exists()
    assert controller.workspace.contact_map.read_bytes() == before
    with pytest.raises(SetupUnavailableError, match="camera changed"):
        controller.start_contact_calibration()
    with pytest.raises(SetupUnavailableError, match="camera changed"):
        controller.apply_bundle()
    # The block survives a process restart; users must explicitly register again.
    restarted = KeyboardSetupController(
        active_artifacts=controller.active_artifacts, camera_config=controller.camera_config,
        frames=controller.frames, states=controller.states, workspace=controller.workspace,
    )
    with pytest.raises(SetupUnavailableError, match="camera changed"):
        restarted.apply_bundle()


def test_camera_config_change_across_restart_blocks_old_staging(tmp_path):
    controller = _controller(tmp_path)
    controller.layout_state()
    _seed_stale_staging_lineage(controller)
    restarted = KeyboardSetupController(
        active_artifacts=controller.active_artifacts,
        camera_config=replace(controller.camera_config, device_index=2, width=640),
        frames=controller.frames, states=controller.states, workspace=controller.workspace,
    )
    with pytest.raises(SetupUnavailableError, match="camera changed"):
        restarted.start_contact_calibration()
    assert restarted.workspace.contact_map.is_file()


def test_setup_router_exposes_layout_and_marker_without_upload(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    app = FastAPI()
    app.include_router(create_setup_router(controller))
    client = TestClient(app)

    page = client.get("/setup")
    layout = client.get("/api/setup/layout")
    marker = client.get("/api/setup/marker/1.png?size=128")

    assert page.status_code == 200
    assert 'type="file"' not in page.text
    assert layout.json()["profile"]["key_count"] == 82
    assert marker.status_code == 200
    assert marker.content.startswith(b"\x89PNG")
    assert not controller.workspace.root.exists()
    # Explicit start may reuse the existing compatible active anchor.
    assert client.post("/api/setup/contact/start", json={}).status_code == 200


def test_unavailable_setup_workspace_does_not_block_page_construction(
    tmp_path: Path,
) -> None:
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("occupied", encoding="utf-8")
    controller = KeyboardSetupController(
        active_artifacts=_active_bundle(tmp_path),
        camera_config=CameraConfig(),
        frames=LatestFrameStore(),
        states=LatestSceneStateStore(),
        workspace=SetupWorkspace(blocked_parent / "setup"),
    )
    app = FastAPI()
    app.include_router(create_setup_router(controller))
    client = TestClient(app)

    assert client.get("/setup").status_code == 200
    response = client.get("/api/setup/layout")
    assert response.status_code == 200
    response = client.put("/api/setup/layout", json=response.json()["profile"])
    assert response.status_code == 503
    assert "workspace is unavailable" in response.json()["detail"]


def test_setup_workspace_cannot_overlap_active_keyboard_bundle(
    tmp_path: Path,
) -> None:
    active = _active_bundle(tmp_path)
    production_before = {
        path: path.read_bytes()
        for path in (
            active.layout_profile,
            active.anchor_reference,
            active.contact_map,
            active.model_glb,
            active.model_manifest,
        )
    }

    with pytest.raises(ValueError, match="separate from production artifacts"):
        KeyboardSetupController(
            active_artifacts=active,
            camera_config=CameraConfig(),
            frames=LatestFrameStore(),
            states=LatestSceneStateStore(),
            workspace=SetupWorkspace(active.layout_profile.parent),
        )

    assert {path: path.read_bytes() for path in production_before} == production_before


def test_finalized_staging_bundle_applies_atomically(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    save_anchor_reference(
        controller.workspace.anchor,
        load_anchor_reference(controller.active_artifacts.anchor_reference),
    )
    save_contact_map(
        controller.workspace.contact_map,
        load_contact_map(controller.active_artifacts.contact_map),
    )

    result = controller.apply_bundle()

    assert result["applied"] is True
    assert result["restart_required"] is True
    assert result["key_count"] == 82
    assert controller.active_artifacts.model_glb.read_bytes().startswith(b"glTF")


def test_new_anchor_registration_invalidates_stale_staging_lineage(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    _seed_stale_staging_lineage(controller)

    controller.start_anchor_registration()

    assert not controller.workspace.anchor.exists()
    assert not controller.workspace.draft.exists()
    assert not controller.workspace.contact_map.exists()
    with pytest.raises(SetupUnavailableError, match="finalized anchor"):
        controller.apply_bundle()


def test_reset_anchor_registration_invalidates_stale_staging_lineage(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    controller.start_anchor_registration()
    _seed_stale_staging_lineage(controller)

    controller.start_anchor_registration(reset=True)

    assert not controller.workspace.anchor.exists()
    assert not controller.workspace.draft.exists()
    assert not controller.workspace.contact_map.exists()
    with pytest.raises(SetupUnavailableError, match="finalized anchor"):
        controller.apply_bundle()


def test_setup_script_serializes_actions_and_cancels_capture_countdown() -> None:
    script = (ROOT / "src/deskvision/web/static/setup.js").read_text(encoding="utf-8")

    assert "let actionBusy = false;" in script
    assert "if (actionBusy) return;" in script
    assert "if (anchorObservePromise) return anchorObservePromise;" in script
    assert ".finally(()=>{ anchorObservePromise=null; });" in script
    assert 'event.key==="Escape"' in script
    assert 'window.addEventListener("blur",cancelCaptureCountdown)' in script
    assert 'document.addEventListener("visibilitychange"' in script
    assert "renderMarkers(profile.anchors)" in script
    assert "for(let id=0;id<6;id++)" not in script
    assert 'request("/api/config")' in script
    for action_id in (
        "start-anchor",
        "reset-anchor",
        "start-contact",
        "capture-contact",
        "undo-contact",
        "reset-contact",
    ):
        assert f'"{action_id}"' in script


def test_bundle_builder_rejects_input_output_path_collision(tmp_path: Path) -> None:
    shared = SOURCE / "layout.json"
    destination = KeyboardBundlePaths(
        layout=shared,
        anchor=tmp_path / "anchor.json",
        contact_map=tmp_path / "contact.json",
        model=tmp_path / "keyboard.glb",
        manifest=tmp_path / "manifest.json",
    )

    with pytest.raises(ValueError, match="must be unique"):
        build_keyboard_bundle(
            source_layout=shared,
            source_anchor=SOURCE / "anchor_reference.json",
            source_contact_map=SOURCE / "contact_map.json",
            destination=destination,
        )


def test_setup_uses_atomic_processed_bundle_when_raw_capture_is_ahead(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    acquired_at_ns = 1_000_000
    processed = FramePacket(
        "camera",
        7,
        acquired_at_ns,
        4,
        2,
        np.zeros((2, 4, 3), dtype=np.uint8),
    )
    state = SceneState(
        SCENE_STATE_SCHEMA_VERSION,
        "camera",
        7,
        acquired_at_ns,
    )
    controller.states.publish(state)
    controller.states.bundles.publish(processed, state)
    controller.frames.publish(
        FramePacket(
            "camera",
            9,
            acquired_at_ns + 2,
            4,
            2,
            np.ones((2, 4, 3), dtype=np.uint8),
        )
    )

    frame, paired_state = controller._latest_same_frame_pair()

    assert frame is processed
    assert paired_state is state


def test_setup_overview_and_layout_are_read_only_for_calibrated_keyboard(tmp_path):
    controller = _controller(tmp_path)
    app = FastAPI()
    app.include_router(create_setup_router(controller))
    client = TestClient(app)
    before = {path: path.read_bytes() for path in (tmp_path / "active").iterdir()}
    for _ in range(3):
        state = client.get("/api/setup/status").json()
        assert state["schema_version"] == "mikotype-setup-status-0.1"
        assert state["active"]["valid"] is True
        assert state["active"]["layout"]["key_count"] == 82
        assert state["active"]["contact"]["captured_samples"] == 410
        assert state["active"]["contact"]["completed_keys"] == 82
        assert state["active"]["model"]["valid"] is True
        assert state["staging"] == {"valid": False}
        assert state["registration"] is None
        assert all(step["complete"] for step in state["steps"].values())
        assert client.get("/api/setup/layout").json()["source"] == "active"
    assert not controller.workspace.root.exists()
    assert {path: path.read_bytes() for path in before} == before


def test_overview_reads_draft_without_starting_or_rewriting_session(tmp_path):
    controller = _controller(tmp_path)
    session = controller.start_contact_calibration()
    assert session["progress"]["captured_samples"] == 0
    controller._contact_session.capture_reference_point((1.0, 2.0))
    before = {path: path.read_bytes() for path in controller.workspace.root.iterdir()}
    restarted = KeyboardSetupController(
        active_artifacts=controller.active_artifacts, camera_config=controller.camera_config,
        frames=controller.frames, states=controller.states, workspace=controller.workspace,
    )
    state = restarted.overview_state()
    assert state["staging"]["contact"]["captured_samples"] == 1
    assert state["staging"]["contact"]["resume_available"] is True
    assert state["steps"]["contact"]["complete"] is False
    assert state["steps"]["apply"]["available"] is False
    assert restarted.contact_state()["status"] == "not_started"
    assert {path: path.read_bytes() for path in before} == before


def test_changed_layout_invalidates_staging_but_preserves_active_and_skippable_same_layout(tmp_path):
    controller = _controller(tmp_path)
    _seed_stale_staging_lineage(controller)
    active_before = controller.active_artifacts.contact_map.read_bytes()
    profile = controller.layout_state()["profile"]
    assert controller.save_layout(profile)["downstream_status"] == "unchanged"
    assert controller.overview_state()["staging"]["valid"] is True
    profile["keys"][0]["width_units"] += 0.1
    result = controller.save_layout(profile)
    assert result["downstream_status"] == "anchor_and_contact_must_be_revalidated"
    state = controller.overview_state()
    assert state["active"]["valid"] is True
    assert state["steps"]["anchor"]["complete"] is False
    assert state["steps"]["contact"]["available"] is False
    assert controller.active_artifacts.contact_map.read_bytes() == active_before
    with pytest.raises(SetupUnavailableError, match="finalize an anchor"):
        controller.start_contact_calibration()


def test_new_registration_stays_incomplete_after_restart_not_active_fallback(tmp_path):
    controller = _controller(tmp_path)
    controller.start_anchor_registration()
    restarted = KeyboardSetupController(
        active_artifacts=controller.active_artifacts, camera_config=controller.camera_config,
        frames=controller.frames, states=controller.states, workspace=controller.workspace,
    )
    assert restarted.overview_state()["steps"]["anchor"]["complete"] is False


def test_overview_detects_incompatible_anchor_assignment(tmp_path):
    controller = _controller(tmp_path)
    _seed_stale_staging_lineage(controller)
    payload = controller.layout_state()["profile"]
    payload["anchors"][0]["key_id"] = "f1"
    from deskvision.calibration.layout_profile import save_layout_profile
    save_layout_profile(controller.workspace.layout, payload)
    state = controller.overview_state()
    assert state["staging"]["anchor"]["valid"] is False
    assert state["staging"]["contact"]["valid"] is False
    assert state["steps"]["apply"]["available"] is False


def test_legacy_camera_binding_missing_vertical_flip_is_unchanged(tmp_path):
    controller = _controller(tmp_path)
    controller.save_layout(controller.layout_state()["profile"])
    path = controller.workspace.root / "camera_binding.json"
    previous = json.loads(path.read_text())
    previous.pop("flip_vertical")
    path.write_text(json.dumps(previous))
    restarted = KeyboardSetupController(
        active_artifacts=controller.active_artifacts, camera_config=controller.camera_config,
        frames=controller.frames, states=controller.states, workspace=controller.workspace,
    )
    before = path.read_bytes()
    assert restarted.runtime_mapping_block_reason() is None
    assert restarted.overview_state()["camera_revalidation_required"] is False
    assert path.read_bytes() == before


def test_actual_camera_flip_blocks_runtime_until_applied_bundle_restarts(tmp_path):
    controller = _controller(tmp_path)
    _seed_stale_staging_lineage(controller)
    with controller.camera_change(replace(controller.camera_config, mirror=True)):
        # Safe to call inside camera_change lock, including from worker join.
        assert controller.runtime_mapping_block_reason() == "camera_changed_recalibration_required"
    assert controller.overview_state()["camera_revalidation_required"] is True
    assert controller.runtime_camera_revalidation_path.is_file()
    # Finalizing anchors clears staging gate only, never reactivates old runtime.
    controller.camera_revalidation_path.unlink()
    assert controller.runtime_mapping_block_reason() is not None
    controller.apply_bundle()
    assert controller.runtime_mapping_block_reason() is not None
    assert controller.overview_state()["restart_required"] is True
    assert not controller.runtime_camera_revalidation_path.exists()
    restarted = KeyboardSetupController(
        active_artifacts=controller.active_artifacts, camera_config=controller.camera_config,
        frames=controller.frames, states=controller.states, workspace=controller.workspace,
    )
    assert restarted.runtime_mapping_block_reason() is None


@pytest.mark.parametrize("mirror,flip_vertical,raw_right", [
    (False, False, "left"), (True, False, "right"),
    (False, True, "right"), (True, True, "left"),
])
def test_calibration_right_index_respects_input_reflection_parity(tmp_path, mirror, flip_vertical, raw_right):
    controller = _controller(tmp_path)
    controller.camera_config = replace(controller.camera_config, mirror=mirror, flip_vertical=flip_vertical)
    hands = tuple(DetectedHand(side, 1.0, (HandLandmark(0.5, 0.5),) * 21) for side in ("left", "right"))
    assert controller._physical_right_hand(hands).handedness == raw_right


def test_applied_bundle_overview_reports_completion_and_restart(tmp_path):
    controller = _controller(tmp_path)
    _seed_stale_staging_lineage(controller)
    controller.apply_bundle()
    state = controller.overview_state()
    assert state["steps"]["apply"]["complete"] is True
    assert state["restart_required"] is True


def test_runtime_baseline_detects_next_start_camera_change_without_opening_setup(tmp_path):
    controller = _controller(tmp_path)
    assert not controller.workspace.root.exists()
    assert controller.bind_runtime_camera() == {"bound": True, "mapping_blocked": False}
    assert sorted(path.name for path in controller.workspace.root.iterdir()) == ["camera_binding.json"]
    active_before = {path: path.read_bytes() for path in controller.active_artifacts.layout_profile.parent.iterdir()}
    restarted = KeyboardSetupController(
        active_artifacts=controller.active_artifacts,
        camera_config=replace(controller.camera_config, mirror=True),
        frames=controller.frames, states=controller.states, workspace=controller.workspace,
    )
    assert restarted.runtime_mapping_block_reason() == "camera_changed_recalibration_required"
    assert restarted.bind_runtime_camera()["mapping_blocked"] is True
    assert restarted.camera_revalidation_path.is_file()
    assert restarted.runtime_camera_revalidation_path.is_file()
    assert not restarted.workspace.layout.exists()
    assert {path: path.read_bytes() for path in active_before} == active_before


@pytest.mark.parametrize("camera", [CameraConfig(mirror=True), CameraConfig(flip_vertical=True)])
def test_missing_baseline_with_corrected_input_is_conservatively_blocked(tmp_path, camera):
    controller = KeyboardSetupController(
        active_artifacts=_active_bundle(tmp_path), camera_config=camera,
        frames=LatestFrameStore(), states=LatestSceneStateStore(),
        workspace=SetupWorkspace(tmp_path / "staging"),
    )
    assert controller.runtime_mapping_block_reason() == "camera_changed_recalibration_required"
    assert controller.overview_state()["camera_revalidation_required"] is True
    assert not controller.workspace.root.exists()
    assert controller.bind_runtime_camera() == {"bound": True, "mapping_blocked": True}


@pytest.mark.parametrize("broken", ["not JSON", "null", "[]", '{"mirror":false}'])
def test_corrupt_camera_baseline_blocks_then_rebuilds_without_crashing(tmp_path, broken):
    controller = _controller(tmp_path)
    controller.workspace.root.mkdir()
    path = controller.workspace.root / "camera_binding.json"
    path.write_text(broken)
    restarted = KeyboardSetupController(
        active_artifacts=controller.active_artifacts, camera_config=controller.camera_config,
        frames=controller.frames, states=controller.states, workspace=controller.workspace,
    )
    assert restarted.runtime_mapping_block_reason() == "camera_changed_recalibration_required"
    assert path.read_text() == broken
    assert restarted.bind_runtime_camera() == {"bound": True, "mapping_blocked": True}
    assert json.loads(path.read_text()) == restarted._camera_identity(restarted.camera_config)
    assert restarted.runtime_camera_revalidation_path.is_file()
    assert not restarted.workspace.layout.exists()


def test_unwritable_runtime_baseline_blocks_mapping_without_stopping_ui(tmp_path):
    controller = _controller(tmp_path)
    controller.workspace.root.write_text("not a folder")
    result = controller.bind_runtime_camera()
    assert result["bound"] is False
    assert result["mapping_blocked"] is True
    assert result["error"]
    assert controller.runtime_mapping_block_reason() == "camera_binding_unavailable"


def test_timestamp_only_layout_save_preserves_reuse_and_does_not_reset_calibration(tmp_path):
    from deskvision.calibration.layout_profile import load_layout_profile, save_layout_profile
    controller = _controller(tmp_path)
    active = load_layout_profile(controller.active_artifacts.layout_profile)
    save_layout_profile(controller.workspace.layout, active, timestamp="2040-01-01T00:00:00Z")
    assert load_layout_profile(controller.workspace.layout).content_hash != active.content_hash
    assert controller.overview_state()["steps"]["contact"]["complete"] is True
    result = controller.save_layout(controller.layout_state()["profile"])
    assert result["downstream_status"] == "unchanged"
    assert not controller.staging_lineage_path.exists()
    assert controller.start_contact_calibration()["status"] == "collecting"
