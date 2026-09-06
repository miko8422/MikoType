"""Standalone synthetic video-layer dashboard for test-only validation."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import argparse
import json
import struct
import time
from threading import Lock

import cv2
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from deskvision.core.config import CameraConfig
from deskvision.video.capture import CaptureThread
from deskvision.video.latest_frame import LatestFrameStore
from deskvision.video.mac_camera import MacCameraSource
from tests.ui.synthetic import SyntheticFrameSource


STATIC_DIR = Path(__file__).parent / "static"
PREVIEW_MAX_WIDTH = 960
PREVIEW_JPEG_QUALITY = 70
_PACKET_HEADER = struct.Struct("!I")


class IsolatedRuntime:
    """Own the synthetic source and capture thread for one UI process."""

    def __init__(self, source_mode: str = "synthetic") -> None:
        if source_mode not in {"synthetic", "camera"}:
            raise ValueError("source_mode must be synthetic or camera")
        self.source_mode = source_mode
        self.startup_error: str | None = None
        if source_mode == "camera":
            self.source = MacCameraSource(
                CameraConfig(source_id="test_mac_main"),
            )
        else:
            self.source = SyntheticFrameSource()
        self.store = LatestFrameStore()
        self.capture = CaptureThread(
            self.source,
            self.store,
            stale_frame_threshold_ms=500,
            read_failure_backoff_s=0.01,
        )
        self._preview_lock = Lock()
        self._preview_frame_id: int | None = None
        self._preview_jpeg: bytes | None = None
        self._preview_server_ready_ns: int | None = None

    def preview_jpeg(self, frame) -> tuple[bytes, int]:
        """Encode one latest frame once and share it across test viewers."""
        with self._preview_lock:
            if (
                self._preview_frame_id == frame.frame_id
                and self._preview_jpeg is not None
                and self._preview_server_ready_ns is not None
            ):
                return self._preview_jpeg, self._preview_server_ready_ns
            ok, encoded = _encode_preview(frame.image_bgr)
            if not ok:
                raise RuntimeError("preview frame JPEG encoding failed")
            self._preview_jpeg = encoded.tobytes()
            self._preview_frame_id = frame.frame_id
            self._preview_server_ready_ns = time.time_ns()
            return self._preview_jpeg, self._preview_server_ready_ns

    def preview_packet(self, frame) -> bytes:
        jpeg, server_ready_ns = self.preview_jpeg(frame)
        header = json.dumps(
            {
                "frame_id": frame.frame_id,
                "source_id": frame.source_id,
                "acquired_at_ns": str(frame.acquired_at_ns),
                "server_sent_at_ns": str(server_ready_ns),
                "width": frame.width,
                "height": frame.height,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        return _PACKET_HEADER.pack(len(header)) + header + jpeg

    def close(self) -> None:
        """Release resources owned by this isolated runtime."""
        self.capture.stop()

def create_app(
    runtime: IsolatedRuntime | None = None,
    *,
    static_dir: Path = STATIC_DIR,
    app_title: str = "VR Desk Vision — Isolated Video Layer Test UI",
) -> FastAPI:
    runtime = runtime or IsolatedRuntime()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            try:
                runtime.capture.start()
            except Exception as exc:
                # Keep the diagnostics page available, but do not skip runtime
                # cleanup.  Derived runtimes may already own background workers
                # or native model resources before camera startup is attempted.
                runtime.startup_error = str(exc)
            yield
        finally:
            runtime.close()

    app = FastAPI(
        title=app_title,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.mount(
        "/test-ui-static",
        StaticFiles(directory=STATIC_DIR),
        name="test-ui-static",
    )

    @app.get("/", response_class=FileResponse)
    def index() -> str:
        return str(static_dir / "index.html")

    @app.get("/api/metrics")
    def metrics() -> dict[str, object]:
        snapshot = runtime.capture.metrics().to_dict()
        snapshot["source_mode"] = runtime.source_mode
        snapshot["startup_error"] = runtime.startup_error
        snapshot["production_camera"] = False
        snapshot["production_web_server"] = False
        return snapshot

    @app.get("/api/frame-meta")
    def frame_meta() -> dict[str, object]:
        frame = runtime.store.latest()
        if frame is None:
            raise HTTPException(
                status_code=503,
                detail=runtime.startup_error or "no test frame available yet",
            )
        return {
            "source_id": frame.source_id,
            "frame_id": frame.frame_id,
            "acquired_at_ns": frame.acquired_at_ns,
            "width": frame.width,
            "height": frame.height,
        }

    @app.get("/api/frame.jpg")
    def frame_jpeg() -> Response:
        frame = runtime.store.latest()
        if frame is None:
            raise HTTPException(
                status_code=503,
                detail=runtime.startup_error or "no test frame available yet",
            )
        try:
            jpeg, server_sent_at_ns = runtime.preview_jpeg(frame)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return Response(
            content=jpeg,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "X-Frame-Id": str(frame.frame_id),
                "X-Frame-Acquired-At-Ns": str(frame.acquired_at_ns),
                "X-Frame-Server-Sent-At-Ns": str(server_sent_at_ns),
                "X-Frame-Width": str(frame.width),
                "X-Frame-Height": str(frame.height),
            },
        )

    @app.websocket("/api/stream")
    async def frame_stream(websocket: WebSocket) -> None:
        """Push only the newest preview frame over one low-latency connection."""
        await websocket.accept()
        last_frame_id: int | None = None
        try:
            while True:
                frame = await asyncio.to_thread(
                    runtime.store.wait_for_newer,
                    last_frame_id,
                    timeout_s=1.0,
                )
                if frame is None:
                    continue
                payload = await asyncio.to_thread(runtime.preview_packet, frame)
                await websocket.send_bytes(payload)
                last_frame_id = frame.frame_id
        except WebSocketDisconnect:
            return

    @app.get("/api/stream.mjpeg")
    def frame_mjpeg() -> StreamingResponse:
        """Fallback stream for browsers without WebSocket support."""

        def packets():
            last_frame_id: int | None = None
            while True:
                frame = runtime.store.wait_for_newer(last_frame_id, timeout_s=1.0)
                if frame is None:
                    continue
                try:
                    body, server_ready_ns = runtime.preview_jpeg(frame)
                except RuntimeError:
                    continue
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(body)}\r\n".encode("ascii")
                    + f"X-Frame-Id: {frame.frame_id}\r\n".encode("ascii")
                    + f"X-Frame-Acquired-At-Ns: {frame.acquired_at_ns}\r\n".encode("ascii")
                    + f"X-Frame-Server-Sent-At-Ns: {server_ready_ns}\r\n\r\n".encode("ascii")
                    + body
                    + b"\r\n"
                )
                last_frame_id = frame.frame_id

        return StreamingResponse(
            packets(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def _encode_preview(image_bgr):
    """Resize only the test preview and encode it with a bounded JPEG cost."""
    preview = image_bgr
    height, width = image_bgr.shape[:2]
    if width > PREVIEW_MAX_WIDTH:
        target_height = max(1, round(height * PREVIEW_MAX_WIDTH / width))
        preview = cv2.resize(
            image_bgr,
            (PREVIEW_MAX_WIDTH, target_height),
            interpolation=cv2.INTER_AREA,
        )
    return cv2.imencode(
        ".jpg",
        preview,
        [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_JPEG_QUALITY],
    )


app = create_app()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--source", choices=("synthetic", "camera"), default="synthetic")
    args = parser.parse_args()
    selected_app = app if args.source == "synthetic" else create_app(IsolatedRuntime(args.source))
    uvicorn.run(
        selected_app,
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
