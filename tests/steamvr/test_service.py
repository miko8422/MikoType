from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import time
from zipfile import ZipFile

import cv2
from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest

from deskvision.state.scene_state import ArtifactRevisions, FingertipState, KeyboardModelState, KeyboardPoseState, KeyboardState, KeyHighlightState, SceneState
from deskvision.state.store import LatestSceneStateStore
from deskvision.steamvr.service import DEFAULT_SETTINGS, FRAME_HEADER, SteamVRController
from deskvision.steamvr import diagnostics
from deskvision.web.steamvr import create_steamvr_router


pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def controller():
    root = ROOT / "data/keyboards/kzzi_user_adjustable_82"
    manifest = json.loads((root / "adaptive_keyboard_manifest.json").read_text())
    return SteamVRController(LatestSceneStateStore(), manifest, (root / "adaptive_keyboard.glb").read_bytes())


@pytest.fixture
def client(controller):
    app = FastAPI()
    app.include_router(create_steamvr_router(controller))
    return TestClient(app)


def scene(controller, *, stale=False, usable=True):
    m = controller.manifest
    key = next(iter(controller.keys.values()))
    keyboard = KeyboardState("aruco-anchor-reference-2d", KeyboardPoseState("tracking", usable, 1, 4),
        ArtifactRevisions(m["layout"]["profile_content_hash"], m["layout"]["inventory_revision"], m["contact_binding"]["anchor_revision"], m["contact_binding"]["contact_map_revision"], m["model_revision"]),
        KeyboardModelState(m["model_revision"], controller.sha256, "/model", "/manifest", len(controller.keys)))
    return SceneState("0.2", "test", 5, time.time_ns() - (2_000_000_000 if stale else 0), keyboard=keyboard,
        fingertips=(FingertipState("tip", 0, "right", "index", 1, .5, .5, *key["contact_center_reference"], 1),),
        key_highlights=(KeyHighlightState(key["key_id"], key["node_name"], key["label"], 1, True),))


def test_wire_header_live_rgba_and_revision_hash(controller):
    controller.states.publish(scene(controller))
    controller.update_settings({**DEFAULT_SETTINGS, "enabled": True, "pose_confirmed": True})
    frame = controller.frame()
    h = FRAME_HEADER.unpack_from(frame)
    assert FRAME_HEADER.size == 96
    assert h[:2] == (b"MIKOVR01", 1)
    assert h[4] == 7
    assert h[-1].hex() == controller.sha256
    assert len(frame) == 96 + h[2] * h[3] * 4
    assert any(frame[96:])
    assert FRAME_HEADER.unpack_from(controller.frame())[5] > h[5]


@pytest.mark.parametrize("kind", ["stale", "no_pose", "wrong_model", "wrong_contact", "cleared", "future"])
def test_unsafe_scene_clears_transport_instead_of_sticking(controller, kind):
    state = scene(controller)
    controller.states.publish(state)
    assert FRAME_HEADER.unpack_from(controller.frame())[4] & 1
    if kind == "stale":
        state = replace(state, captured_at_ns=time.time_ns() - 5_000_000_000)
    elif kind == "no_pose":
        state = replace(state, keyboard=replace(state.keyboard, pose=replace(state.keyboard.pose, usable=False)))
    elif kind == "wrong_model":
        state = replace(state, keyboard=replace(state.keyboard, model=replace(state.keyboard.model, sha256="f" * 64)))
    elif kind == "wrong_contact":
        state = replace(state, keyboard=replace(state.keyboard, artifacts=replace(state.keyboard.artifacts, contact_map="different")))
    elif kind == "future":
        state = replace(state, captured_at_ns=time.time_ns() + 1_000_000_000)
    elif kind == "cleared":
        state = SceneState.empty(source_id="test", source_frame_id=6, captured_at_ns=time.time_ns())
    controller.states.publish(state)
    frame = controller.frame()
    assert not FRAME_HEADER.unpack_from(frame)[4] & 1
    assert not any(frame[96:])


def test_ttl_expires_even_without_new_store_generation(controller, monkeypatch):
    state = scene(controller)
    controller.states.publish(state)
    assert FRAME_HEADER.unpack_from(controller.frame())[4] & 1
    monkeypatch.setattr("deskvision.steamvr.service.time.time_ns", lambda: state.captured_at_ns + 2_000_000_000)
    assert not FRAME_HEADER.unpack_from(controller.frame())[4] & 1


def test_calibrated_center_projects_to_corresponding_model_key(controller):
    for key in controller.keys.values():
        assert controller.project_tip(*key["contact_center_reference"]) == pytest.approx((key["center"][0], key["center"][2]))


def test_projection_continuous_near_every_measured_center(controller):
    for key in controller.keys.values():
        x, y = key["contact_center_reference"]
        center = controller.project_tip(x, y)
        nearby = controller.project_tip(x + 1e-6, y - 1e-6)
        assert np.linalg.norm(np.array(center) - nearby) < 1e-6


def test_expired_generation_cannot_revive_after_wall_clock_correction(controller, monkeypatch):
    state = scene(controller)
    controller.states.publish(state)
    assert controller.status()["scene"]["fresh"]
    monkeypatch.setattr("deskvision.steamvr.service.time.time_ns", lambda: state.captured_at_ns + 2_000_000_000)
    assert not controller.status()["scene"]["fresh"]
    monkeypatch.setattr("deskvision.steamvr.service.time.time_ns", lambda: state.captured_at_ns + 100_000_000)
    assert not controller.status()["scene"]["fresh"]


def test_heartbeat_does_not_hide_model_failure(controller):
    controller.ingest({"source": "windows-bridge", "level": "error", "event": "model_mismatch", "message": "model differs"})
    controller.ingest({"source": "windows-bridge", "level": "info", "event": "bridge_heartbeat", "message": "alive"})
    assert controller.status()["bridge"]["status"] == "model_mismatch"


def test_preview_png_and_downloaded_assets_match_same_active_model(client, controller):
    png = client.get("/api/steamvr/preview.png")
    assert png.status_code == 200
    assert cv2.imdecode(np.frombuffer(png.content, dtype=np.uint8), cv2.IMREAD_UNCHANGED).shape[2] == 4
    zipped = client.get("/api/steamvr/driver-assets.zip")
    assert zipped.status_code == 200
    with ZipFile(io.BytesIO(zipped.content)) as archive:
        manifest = json.loads(archive.read("export_manifest.json"))
        assert manifest["source"]["glb_sha256"] == controller.sha256
        assert manifest["render_model"]["name"] == "mikotype_keyboard"
        for name, record in manifest["outputs"].items():
            assert Path(name).name == name
            assert hashlib.sha256(archive.read(name)).hexdigest() == record["sha256"]
    assert client.get("/api/steamvr/driver-assets.zip").content == zipped.content


def test_native_routes_require_session_token(client, controller):
    assert client.get("/api/steamvr/frame.bin").status_code == 401
    assert client.post("/api/steamvr/events", json={}).status_code == 401
    header = {"X-MikoType-SteamVR-Token": controller.token}
    assert client.get("/api/steamvr/frame.bin", headers=header).status_code == 200
    assert client.post("/api/steamvr/events", headers=header, json={"source": "windows-bridge", "level": "info", "event": "bridge_heartbeat", "message": "alive", "details": {"tracker_found": True}}).status_code == 200
    assert client.get("/api/steamvr/status").json()["bridge"]["connected"]
    assert controller.token not in client.get("/api/steamvr/diagnostics").text
    assert client.get("/api/steamvr/bridge-token").text.strip() == controller.token
    assert client.get("/api/steamvr/bridge-token", headers={"sec-fetch-site": "cross-site"}).status_code == 403


def test_logs_bounded_redacted_and_heartbeat_not_log_spam(controller):
    for n in range(400):
        controller.add_event("test", "info", "tick", f"secret={controller.token} n={n}", {"token": controller.token})
    assert len(controller.status()["logs"]) == 300
    assert controller.token not in json.dumps(controller.status())
    controller.ingest({"source": "windows-bridge", "level": "info", "event": "bridge_heartbeat", "message": "alive"})
    assert len(controller.status()["logs"]) == 300
    controller.last_seen = time.monotonic() - 7
    assert not controller.status()["bridge"]["connected"]


@pytest.mark.parametrize("change", [{"y": -3}, {"x": 11}, {"pitch": float("inf")}, {"yaw": float("nan")}, {"enabled": 1}, {"roll": True}, {"extra": 0}])
def test_invalid_settings_do_not_mutate(controller, change):
    with pytest.raises(ValueError):
        controller.update_settings({**DEFAULT_SETTINGS, **change})
    assert controller.settings == DEFAULT_SETTINGS


def test_api_rejects_oversized_and_malformed_events(client, controller):
    headers = {"X-MikoType-SteamVR-Token": controller.token}
    assert client.post("/api/steamvr/events", headers=headers, content=b"x" * 17000).status_code == 413
    assert client.post("/api/steamvr/events", headers=headers, json=[]).status_code == 422
    assert client.post("/api/steamvr/events", headers=headers, json={"source": "untrusted"}).status_code == 422


def test_user_headset_feedback_in_diagnostics(client):
    assert client.post("/api/steamvr/feedback", json={"message": "Pimax Home 中只有轮廓，高亮可见"}).status_code == 200
    assert "Pimax" in client.get("/api/steamvr/diagnostics").text


def test_windows_log_collection_explicit_allowlisted_bounded_and_redacted(tmp_path):
    secret = "sensitive-secret-value"
    (tmp_path / "vrserver.txt").write_text("ignore\n" * 10000 + "MikoType token=" + secret + " C:\\Users\\Alice\\Steam\n")
    (tmp_path / "private.txt").write_text("must not read")
    result = diagnostics.read_log_tails(tmp_path, token=secret)
    assert len(result["files"]) == 4
    assert result["files"][0]["status"] == "collected"
    assert len(result["files"][0]["lines"]) <= 100
    text = json.dumps(result)
    assert secret not in text and "Alice" not in text and "must not read" not in text


def test_log_symlink_not_followed(tmp_path):
    target = tmp_path / "private.txt"
    target.write_text("private")
    (tmp_path / "vrserver.txt").symlink_to(target)
    assert diagnostics.read_log_tails(tmp_path)["files"][0]["status"] == "unsafe_link_skipped"


def test_mac_collect_does_not_import_windows_runtime(client, monkeypatch):
    monkeypatch.setattr(diagnostics.sys, "platform", "darwin")
    assert client.post("/api/steamvr/collect-logs").json()["status"] == "windows_only"


def test_steamvr_inherits_real_console_security_boundary(controller, tmp_path):
    from tests.web.test_debug_app import _context
    from deskvision.web.app import create_debug_app

    app = create_debug_app(_context(tmp_path))
    app.include_router(create_steamvr_router(controller))
    client = TestClient(app, base_url="http://127.0.0.1:9000")
    assert client.get("/api/steamvr/status").status_code == 200
    assert client.get("/api/steamvr/status", headers={"host": "evil.example:9000"}).status_code == 403
    assert client.put("/api/steamvr/settings", headers={"origin": "https://evil.example"}, json=DEFAULT_SETTINGS).status_code == 403
    assert client.get("/api/steamvr/bridge-token", headers={"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document"}).status_code == 403
    native = {"X-MikoType-SteamVR-Token": controller.token}
    assert client.get("/api/steamvr/frame.bin", headers=native).status_code == 200
    assert client.get("/api/steamvr/status").headers["cache-control"] == "no-store"


def test_bridge_detail_storage_is_bounded_over_long_running_events(controller):
    for i in range(200):
        controller.ingest({"source": "windows-bridge", "level": "info", "event": "bridge_heartbeat", "message": "alive", "details": {f"key_{i}": i}})
    assert len(controller.status()["bridge"]["details"]) <= 20


def test_startup_does_not_reuse_pre_steamvr_service(tmp_path):
    from deskvision.web.binding import service_identity_mismatch

    mismatch = service_identity_mismatch(
        {"capabilities": ["runtime_inspector", "runtime_settings", "keyboard_setup"]},
        config_path=tmp_path / "config.yaml", workspace_path=tmp_path, config_revision="rev")
    assert "steamvr_observability" in mismatch
