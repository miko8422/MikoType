import json
from pathlib import Path
import time

import cv2
from fastapi.testclient import TestClient
import numpy as np
import pytest
from starlette.websockets import WebSocketDisconnect

from deskvision.core.models import FramePacket
from deskvision.observability.health import HealthSnapshot
from deskvision.perception.worker import PerceptionWorkerStats
from deskvision.state.scene_state import SCENE_STATE_SCHEMA_VERSION, SceneState
from deskvision.state.store import LatestSceneStateStore
from deskvision.video.jpeg_encoder import LatestJpegEncoder
from deskvision.video.latest_frame import LatestFrameStore
from deskvision.web.app import DebugWebContext, create_debug_app


pytestmark = pytest.mark.unit
BASE_URL = "http://127.0.0.1:9000"


def _context(tmp_path: Path) -> DebugWebContext:
    layout = tmp_path / "layout.json"
    manifest = tmp_path / "manifest.json"
    model = tmp_path / "keyboard.glb"
    layout.write_text(json.dumps({"keys": []}), encoding="utf-8")
    manifest.write_text(json.dumps({"model_revision": "rev"}), encoding="utf-8")
    model.write_bytes(b"glTF")
    frames = LatestFrameStore()
    acquired_at_ns = time.time_ns()
    frame = FramePacket(
        "camera", 1, acquired_at_ns, 4, 2, np.zeros((2, 4, 3), dtype=np.uint8)
    )
    frames.publish(frame)
    states = LatestSceneStateStore()
    state = SceneState(
        SCENE_STATE_SCHEMA_VERSION,
        "camera",
        1,
        acquired_at_ns,
        emitted_at_ns=time.time_ns(),
    )
    states.publish(state)
    states.bundles.publish(frame, state)
    return DebugWebContext(
        frames=frames,
        states=states,
        encoder=LatestJpegEncoder(),
        capture_metrics=lambda: HealthSnapshot(
            "streaming", True, 30.0, 1, 2.0, (4, 2)
        ),
        perception_metrics=lambda: PerceptionWorkerStats(
            "running", 1, 0, 1, 3.0, None
        ),
        layout_path=layout,
        model_path=model,
        manifest_path=manifest,
        bundle_stale_after_ms=250,
    )


def _client(context: DebugWebContext) -> TestClient:
    return TestClient(create_debug_app(context), base_url=BASE_URL)


def test_debug_app_exposes_state_health_and_artifacts(tmp_path: Path) -> None:
    client = _client(_context(tmp_path))

    assert client.get("/").status_code == 200
    assert client.get("/api/state").json()["schema_version"] == "0.2"
    assert client.get("/api/health").json()["perception"]["status"] == "running"
    assert client.get("/api/health").json()["bundle_store"]["published_count"] == 1
    assert client.get("/api/layout").json() == {"keys": []}
    assert client.get("/api/model/manifest").json()["model_revision"] == "rev"
    assert client.get("/api/model/keyboard.glb").content == b"glTF"
    snapshot = client.get("/snapshot.jpg")
    assert snapshot.status_code == 200
    assert snapshot.headers["x-frame-id"] == "1"


def test_snapshot_uses_processed_bundle_not_newer_raw_capture(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.frames.publish(
        FramePacket(
            "camera",
            2,
            time.time_ns(),
            4,
            2,
            np.full((2, 4, 3), 255, dtype=np.uint8),
        )
    )

    response = _client(context).get("/snapshot.jpg")

    assert response.status_code == 200
    assert response.headers["x-frame-id"] == "1"
    decoded = cv2.imdecode(np.frombuffer(response.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None
    assert int(decoded.max()) == 0


def test_bundle_websocket_pairs_metadata_and_exact_jpeg(tmp_path: Path) -> None:
    context = _context(tmp_path)
    client = _client(context)

    with client.websocket_connect(
        "/ws/bundle",
        headers={"Host": "127.0.0.1:9000"},
    ) as websocket:
        metadata = websocket.receive_json()
        jpeg = websocket.receive_bytes()

    assert metadata["type"] == "frame_state_bundle"
    assert metadata["generation"] == 1
    assert metadata["frame"]["frame_id"] == metadata["state"]["source_frame_id"] == 1
    assert metadata["frame"]["acquired_at_ns"] == metadata["state"]["captured_at_ns"]
    assert metadata["frame"]["byte_length"] == len(jpeg)
    assert cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR) is not None


def test_bundle_websocket_marks_unchanged_bundle_stale(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context = DebugWebContext(
        frames=context.frames,
        states=context.states,
        encoder=context.encoder,
        capture_metrics=context.capture_metrics,
        perception_metrics=context.perception_metrics,
        layout_path=context.layout_path,
        model_path=context.model_path,
        manifest_path=context.manifest_path,
        bundle_stale_after_ms=25,
    )
    client = _client(context)

    with client.websocket_connect(
        "/ws/bundle",
        headers={"Host": "127.0.0.1:9000"},
    ) as websocket:
        assert websocket.receive_json()["type"] == "frame_state_bundle"
        websocket.receive_bytes()
        stale = websocket.receive_json()

    assert stale == {
        "type": "stale",
        "reason": "processed frame bundle timed out",
    }


def test_inspector_uses_atomic_bundle_socket_instead_of_mjpeg_image(tmp_path: Path) -> None:
    client = _client(_context(tmp_path))

    page = client.get("/").text
    script = client.get("/static/app.js").text

    assert 'id="camera" alt=' in page
    assert 'id="camera" src="/stream.mjpg"' not in page
    assert "/ws/bundle" in script
    assert "clearLiveDisplay" in script
    assert 'href="/settings"' in page
    assert 'href="/setup"' in page


def test_debug_app_fails_closed_without_state(tmp_path: Path) -> None:
    context = _context(tmp_path)
    empty = LatestSceneStateStore()
    context = DebugWebContext(
        frames=context.frames,
        states=empty,
        encoder=context.encoder,
        capture_metrics=context.capture_metrics,
        perception_metrics=context.perception_metrics,
        layout_path=context.layout_path,
        model_path=context.model_path,
        manifest_path=context.manifest_path,
        bundle_stale_after_ms=context.bundle_stale_after_ms,
    )

    response = _client(context).get("/api/state")

    assert response.status_code == 503
    assert response.json()["ready"] is False


def test_control_plane_rejects_cross_origin_browser_mutation(tmp_path: Path) -> None:
    app = create_debug_app(_context(tmp_path))

    @app.post("/mutation-probe")
    def mutation_probe() -> dict[str, bool]:
        return {"changed": True}

    client = TestClient(app, base_url=BASE_URL)

    rejected = client.post(
        "/mutation-probe",
        headers={"Origin": "https://untrusted.example"},
    )
    accepted = client.post(
        "/mutation-probe",
        headers={"Origin": BASE_URL},
    )

    assert rejected.status_code == 403
    assert accepted.json() == {"changed": True}


def test_control_plane_rejects_dns_rebinding_host(tmp_path: Path) -> None:
    client = _client(_context(tmp_path))

    response = client.get(
        "/api/service",
        headers={"Host": "untrusted.example:9000"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "unexpected local service host"


def test_camera_get_rejects_cross_site_embedding_and_sets_resource_policy(
    tmp_path: Path,
) -> None:
    client = _client(_context(tmp_path))

    rejected = client.get(
        "/snapshot.jpg",
        headers={
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Dest": "image",
        },
    )
    accepted = client.get("/snapshot.jpg")

    assert rejected.status_code == 403
    assert "cross-site" in rejected.json()["detail"]
    assert accepted.status_code == 200
    assert accepted.headers["cross-origin-resource-policy"] == "same-origin"
    assert accepted.headers["x-frame-options"] == "DENY"


def test_bundle_websocket_rejects_cross_origin_browser(tmp_path: Path) -> None:
    client = _client(_context(tmp_path))

    with pytest.raises(WebSocketDisconnect) as raised:
        with client.websocket_connect(
            "/ws/bundle",
            headers={
                "Host": "127.0.0.1:9000",
                "Origin": "https://untrusted.example",
            },
        ):
            pass

    assert raised.value.code == 1008


def test_runtime_snapshots_do_not_change_when_bundle_files_are_replaced(
    tmp_path: Path,
) -> None:
    base = _context(tmp_path)
    context = DebugWebContext(
        frames=base.frames,
        states=base.states,
        encoder=base.encoder,
        capture_metrics=base.capture_metrics,
        perception_metrics=base.perception_metrics,
        layout_path=base.layout_path,
        model_path=base.model_path,
        manifest_path=base.manifest_path,
        layout_snapshot={"keys": [{"key_id": "old"}]},
        model_snapshot=b"glTF-old",
        manifest_snapshot={"model_revision": "old"},
    )
    app = create_debug_app(context)
    base.layout_path.write_text('{"keys":[{"key_id":"new"}]}', encoding="utf-8")
    base.model_path.write_bytes(b"glTF-new")
    base.manifest_path.write_text('{"model_revision":"new"}', encoding="utf-8")
    client = TestClient(app, base_url=BASE_URL)

    assert client.get("/api/layout").json()["keys"][0]["key_id"] == "old"
    assert client.get("/api/model/keyboard.glb").content == b"glTF-old"
    assert client.get("/api/model/manifest").json()["model_revision"] == "old"
