"""Development-only production inspector for video, mapping, and 3D state."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from copy import deepcopy
import json
from pathlib import Path
import time
from typing import Callable, Mapping
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from deskvision.observability.health import HealthSnapshot
from deskvision.perception.worker import PerceptionWorkerStats
from deskvision.state.bundle import FrameStateBundle
from deskvision.state.store import LatestSceneStateStore
from deskvision.state.scene_state import Diagnostics, SceneState
from deskvision.video.jpeg_encoder import LatestJpegEncoder
from deskvision.video.latest_frame import LatestFrameStore


STATIC_DIR = Path(__file__).parent / "static"


@dataclass(frozen=True, slots=True)
class DebugWebContext:
    frames: LatestFrameStore
    states: LatestSceneStateStore
    encoder: LatestJpegEncoder
    capture_metrics: Callable[[], HealthSnapshot]
    perception_metrics: Callable[[], PerceptionWorkerStats]
    layout_path: Path
    model_path: Path
    manifest_path: Path
    service_host: str = "127.0.0.1"
    service_port: int = 9000
    mirror_preview: bool = True
    max_preview_fps: int = 30
    expose_model_download: bool = True
    bundle_stale_after_ms: int = 1000
    health_interval_ms: int = 1000
    layout_snapshot: Mapping[str, object] | None = None
    model_snapshot: bytes | None = None
    manifest_snapshot: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.max_preview_fps <= 0:
            raise ValueError("max_preview_fps must be positive")
        if self.bundle_stale_after_ms <= 0:
            raise ValueError("bundle_stale_after_ms must be positive")
        if self.health_interval_ms <= 0:
            raise ValueError("health_interval_ms must be positive")
        if not 1 <= self.service_port <= 65535:
            raise ValueError("service_port must be between 1 and 65535")
        if self.model_snapshot is not None and not isinstance(
            self.model_snapshot, bytes
        ):
            raise TypeError("model_snapshot must be bytes")


def _authority_matches(
    authority: str | None,
    *,
    expected_host: str,
    expected_port: int,
) -> bool:
    if not authority:
        return False
    try:
        parsed = urlsplit(f"//{authority}")
        port = parsed.port or 80
    except ValueError:
        return False
    return bool(
        parsed.hostname
        and parsed.hostname.casefold() == expected_host.casefold()
        and port == expected_port
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


def _origin_matches(
    origin: str,
    *,
    expected_host: str,
    expected_port: int,
) -> bool:
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return bool(
        parsed.scheme == "http"
        and _authority_matches(
            parsed.netloc,
            expected_host=expected_host,
            expected_port=expected_port,
        )
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _browser_request_allowed(
    host: str | None,
    origin: str | None,
    context: DebugWebContext,
) -> bool:
    if not _authority_matches(
        host,
        expected_host=context.service_host,
        expected_port=context.service_port,
    ):
        return False
    return origin is None or _origin_matches(
        origin,
        expected_host=context.service_host,
        expected_port=context.service_port,
    )


def _read_json(path: Path, *, artifact: str) -> Mapping[str, object]:
    if not path.is_file():
        raise HTTPException(status_code=503, detail=f"{artifact} is not ready")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"{artifact} cannot be read: {exc}",
        ) from exc
    if not isinstance(payload, Mapping):
        raise HTTPException(status_code=503, detail=f"{artifact} is invalid")
    return payload


def create_debug_app(context: DebugWebContext) -> FastAPI:
    """Build an inspector app without starting or owning the runtime."""

    app = FastAPI(title="MikoType Local Inspector", version="0.2")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def protect_local_console(request: Request, call_next):
        """Pin Host and reject untrusted browser access to the local console."""

        if not _authority_matches(
            request.headers.get("host"),
            expected_host=context.service_host,
            expected_port=context.service_port,
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "unexpected local service host"},
            )

        fetch_site = request.headers.get("sec-fetch-site", "").casefold()
        fetch_mode = request.headers.get("sec-fetch-mode", "").casefold()
        fetch_dest = request.headers.get("sec-fetch-dest", "").casefold()
        top_level_navigation = fetch_mode == "navigate" and fetch_dest == "document"
        if fetch_site in {"cross-site", "same-site"} and not top_level_navigation:
            return JSONResponse(
                status_code=403,
                content={"detail": "cross-site local resource request rejected"},
            )

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and not _origin_matches(
                origin,
                expected_host=context.service_host,
                expected_port=context.service_port,
            ):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "cross-origin control request rejected"},
                )
        response = await call_next(request)
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        # Local iteration must not silently combine an old UI with a new API.
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/config")
    def config() -> dict[str, object]:
        return {
            "schema_version": "deskvision-debug-config-0.1",
            "mirror_preview": context.mirror_preview,
            "max_preview_fps": context.max_preview_fps,
            "key_semantics": "likely-contact-not-mechanical-keypress",
            "model_download_enabled": context.expose_model_download,
            "bundle_stale_after_ms": context.bundle_stale_after_ms,
            "health_interval_ms": context.health_interval_ms,
        }

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "schema_version": "deskvision-health-0.2",
            "capture": context.capture_metrics().to_dict(),
            "perception": asdict(context.perception_metrics()),
            "state_store": asdict(context.states.stats()),
            "bundle_store": asdict(context.states.bundles.stats()),
            "jpeg_encoder": asdict(context.encoder.stats()),
        }

    @app.get("/api/state")
    def state() -> Response:
        bundle = context.states.bundles.latest()
        if bundle is None:
            return JSONResponse(
                status_code=503,
                content={"ready": False, "reason": "no processed frame bundle yet"},
            )
        if _bundle_is_stale(bundle, context.bundle_stale_after_ms):
            return JSONResponse(
                status_code=503,
                content={"ready": False, "reason": "processed frame bundle is stale"},
            )
        return JSONResponse(bundle.state.to_dict())

    @app.get("/api/layout")
    def layout() -> Mapping[str, object]:
        if context.layout_snapshot is not None:
            return deepcopy(context.layout_snapshot)
        return _read_json(context.layout_path, artifact="keyboard layout")

    @app.get("/api/model/manifest")
    def model_manifest() -> Mapping[str, object]:
        if context.manifest_snapshot is not None:
            return deepcopy(context.manifest_snapshot)
        return _read_json(context.manifest_path, artifact="keyboard model manifest")

    @app.get("/api/model/keyboard.glb")
    def keyboard_model() -> Response:
        if not context.expose_model_download:
            raise HTTPException(status_code=404, detail="model download is disabled")
        if context.model_snapshot is not None:
            return Response(
                context.model_snapshot,
                media_type="model/gltf-binary",
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{context.model_path.name}"'
                    ),
                    "Cache-Control": "no-store",
                },
            )
        if not context.model_path.is_file():
            raise HTTPException(status_code=503, detail="keyboard model is not ready")
        return FileResponse(
            context.model_path,
            media_type="model/gltf-binary",
            filename=context.model_path.name,
        )

    @app.get("/snapshot.jpg")
    def snapshot() -> Response:
        bundle = context.states.bundles.latest()
        if bundle is None:
            raise HTTPException(status_code=503, detail="no processed frame bundle yet")
        if _bundle_is_stale(bundle, context.bundle_stale_after_ms):
            raise HTTPException(status_code=503, detail="processed frame bundle is stale")
        frame = bundle.frame
        return Response(
            context.encoder.encode(frame),
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "X-Source-Id": frame.source_id,
                "X-Frame-Id": str(frame.frame_id),
                "X-Acquired-At-Ns": str(frame.acquired_at_ns),
            },
        )

    def mjpeg_frames():
        after_frame_id: int | None = None
        minimum_period_s = 1.0 / context.max_preview_fps
        next_emit_at = 0.0
        while True:
            frame = context.frames.wait_for_newer(after_frame_id, timeout_s=1.0)
            if frame is None or (
                after_frame_id is not None and frame.frame_id <= after_frame_id
            ):
                continue
            now = time.monotonic()
            if now < next_emit_at:
                time.sleep(next_emit_at - now)
                newest = context.frames.latest()
                if newest is not None and newest.frame_id > frame.frame_id:
                    frame = newest
            jpeg = context.encoder.encode(frame)
            after_frame_id = frame.frame_id
            next_emit_at = time.monotonic() + minimum_period_s
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                + f"X-Frame-Id: {frame.frame_id}\r\n".encode("ascii")
                + f"X-Acquired-At-Ns: {frame.acquired_at_ns}\r\n\r\n".encode(
                    "ascii"
                )
                + jpeg
                + b"\r\n"
            )

    @app.get("/stream.mjpg")
    def stream() -> StreamingResponse:
        return StreamingResponse(
            mjpeg_frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.websocket("/ws/state")
    async def websocket_state(websocket: WebSocket) -> None:
        if not _browser_request_allowed(
            websocket.headers.get("host"),
            websocket.headers.get("origin"),
            context,
        ):
            await websocket.close(code=1008, reason="untrusted local console origin")
            return
        await websocket.accept()
        generation, latest = context.states.latest_with_generation()
        try:
            if latest is not None:
                await websocket.send_json(latest.to_dict())
            while True:
                next_generation, latest = await asyncio.to_thread(
                    context.states.wait_for_newer,
                    generation,
                    timeout_s=5.0,
                )
                if next_generation > generation:
                    generation = next_generation
                    if latest is None:
                        latest = SceneState.empty(
                            source_id="camera-transition", source_frame_id=0,
                            captured_at_ns=time.time_ns(),
                            diagnostics=Diagnostics(status="stale", error="camera state cleared"),
                        )
                    await websocket.send_json(latest.to_dict())
        except WebSocketDisconnect:
            return

    async def send_bundle(
        websocket: WebSocket,
        generation: int,
        bundle: FrameStateBundle,
    ) -> None:
        jpeg = await asyncio.to_thread(context.encoder.encode, bundle.frame)
        await websocket.send_json(
            {
                "type": "frame_state_bundle",
                "schema_version": "deskvision-frame-state-bundle-wire-0.1",
                "generation": generation,
                "frame": {
                    "source_id": bundle.frame.source_id,
                    "frame_id": bundle.frame.frame_id,
                    "acquired_at_ns": bundle.frame.acquired_at_ns,
                    "width": bundle.frame.width,
                    "height": bundle.frame.height,
                    "content_type": "image/jpeg",
                    "byte_length": len(jpeg),
                },
                "state": bundle.state.to_dict(),
            }
        )
        await websocket.send_bytes(jpeg)

    @app.websocket("/ws/bundle")
    async def websocket_bundle(websocket: WebSocket) -> None:
        """Send metadata/state followed by its exact JPEG as one logical unit."""

        if not _browser_request_allowed(
            websocket.headers.get("host"),
            websocket.headers.get("origin"),
            context,
        ):
            await websocket.close(code=1008, reason="untrusted local console origin")
            return
        await websocket.accept()
        generation, latest = context.states.bundles.latest_with_generation()
        stale_notified = False
        timeout_s = context.bundle_stale_after_ms / 1000.0
        minimum_period_s = 1.0 / context.max_preview_fps
        last_emit_at = 0.0
        try:
            if latest is not None and not _bundle_is_stale(
                latest, context.bundle_stale_after_ms
            ):
                await send_bundle(websocket, generation, latest)
                last_emit_at = time.monotonic()
            else:
                await websocket.send_json(
                    {
                        "type": "stale",
                        "reason": "no fresh processed frame bundle",
                    }
                )
                stale_notified = True
            while True:
                next_generation, latest = await asyncio.to_thread(
                    context.states.bundles.wait_for_newer,
                    generation,
                    timeout_s=timeout_s,
                )
                if next_generation <= generation:
                    if not stale_notified:
                        await websocket.send_json(
                            {
                                "type": "stale",
                                "reason": "processed frame bundle timed out",
                            }
                        )
                        stale_notified = True
                    continue
                delay_s = minimum_period_s - (time.monotonic() - last_emit_at)
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
                    # Drop superseded bundles while throttling; never send the
                    # older object selected before the delay.
                    next_generation, latest = (
                        context.states.bundles.latest_with_generation()
                    )
                generation = next_generation
                if latest is None or _bundle_is_stale(
                    latest, context.bundle_stale_after_ms
                ):
                    if not stale_notified:
                        await websocket.send_json(
                            {
                                "type": "stale",
                                "reason": "processed frame bundle is stale",
                            }
                        )
                        stale_notified = True
                    continue
                await send_bundle(websocket, generation, latest)
                last_emit_at = time.monotonic()
                stale_notified = False
        except WebSocketDisconnect:
            return

    return app


def _bundle_is_stale(bundle: FrameStateBundle, stale_after_ms: int) -> bool:
    age_ms = max(0.0, (time.time_ns() - bundle.state.emitted_at_ns) / 1_000_000.0)
    return age_ms > stale_after_ms


__all__ = ["DebugWebContext", "create_debug_app"]
