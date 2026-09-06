"""Isolated test UI route and endpoint checks."""

from __future__ import annotations

import json
import struct
import time

from fastapi.testclient import TestClient
import pytest

from deskvision.video.mac_camera import MacCameraSource
from tests.ui.app import IsolatedRuntime, create_app


pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    """Start and stop the isolated synthetic UI around one test."""
    with TestClient(create_app()) as test_client:
        yield test_client


def test_camera_mode_is_constructed_only_in_isolated_runtime() -> None:
    runtime = IsolatedRuntime("camera")
    assert runtime.source_mode == "camera"
    assert isinstance(runtime.source, MacCameraSource)


def test_metrics_and_frame_endpoints_use_synthetic_runtime(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "Video Layer Observatory" in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/low-latency.js").status_code == 200
    assert client.get("/static/popout.html").status_code == 200
    assert client.get("/static/popout.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200

    deadline = time.monotonic() + 1.0
    response = None
    while time.monotonic() < deadline:
        response = client.get("/api/frame.jpg")
        if response.status_code == 200:
            break
        time.sleep(0.02)

    assert response is not None
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(bytes([0xFF, 0xD8]))
    assert "X-Frame-Id" in response.headers
    assert "X-Frame-Acquired-At-Ns" in response.headers
    assert "X-Frame-Server-Sent-At-Ns" in response.headers
    assert "X-Frame-Width" in response.headers
    assert "X-Frame-Height" in response.headers

    metrics = client.get("/api/metrics")
    assert metrics.status_code == 200
    payload = metrics.json()
    assert payload["source_mode"] == "synthetic"
    assert not payload["production_camera"]
    assert payload["frames_captured"] >= 1
    assert payload["overwritten_frames"] >= 0

    with client.websocket_connect("/api/stream") as websocket:
        packet = websocket.receive_bytes()
        header_length = struct.unpack("!I", packet[:4])[0]
        header_start = 4
        header_end = header_start + header_length
        stream_header = json.loads(packet[header_start:header_end])
        jpeg = packet[header_end:]
        assert stream_header["frame_id"] >= 0
        assert stream_header["acquired_at_ns"]
        assert stream_header["server_sent_at_ns"]
        assert jpeg.startswith(bytes([0xFF, 0xD8]))


def test_preview_encoding_is_reused_for_one_frame() -> None:
    runtime = IsolatedRuntime("synthetic")
    with TestClient(create_app(runtime)):
        deadline = time.monotonic() + 1.0
        while runtime.store.latest() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        frame = runtime.store.latest()
        assert frame is not None
        first_jpeg, first_ready_ns = runtime.preview_jpeg(frame)
        second_jpeg, second_ready_ns = runtime.preview_jpeg(frame)
        assert first_jpeg is second_jpeg
        assert first_ready_ns == second_ready_ns
