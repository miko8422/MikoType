from pathlib import Path

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
    save_anchor_reference(
        controller.workspace.anchor,
        load_anchor_reference(controller.active_artifacts.anchor_reference),
    )
    save_contact_map(
        controller.workspace.contact_map,
        load_contact_map(controller.active_artifacts.contact_map),
    )
    controller.workspace.draft.write_text("stale draft", encoding="utf-8")


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
    assert client.post("/api/setup/contact/start", json={}).status_code == 409


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
