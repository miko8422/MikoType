"""Same-origin WebUI plus token-scoped native SteamVR bridge endpoints."""

from __future__ import annotations

import json
from pathlib import Path
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from deskvision.steamvr.service import SteamVRController


STATIC_DIR = Path(__file__).parent / "static"


def create_steamvr_router(controller: SteamVRController) -> APIRouter:
    router = APIRouter()

    def authorize(request: Request) -> None:
        token = request.headers.get("x-mikotype-steamvr-token", "")
        if not secrets.compare_digest(token.encode(), controller.token.encode()):
            raise HTTPException(401, "bridge token required; download a new token after service restart")

    async def payload(request: Request) -> dict:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 16 * 1024:
                raise HTTPException(413, "request exceeds 16 KiB")
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("expected JSON object")
            return value
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(422, "expected valid JSON object") from exc

    @router.get("/steamvr", response_class=HTMLResponse)
    def page():
        return HTMLResponse((STATIC_DIR / "steamvr.html").read_text(encoding="utf-8"))

    @router.get("/api/steamvr/status")
    def status():
        return controller.status()

    @router.put("/api/steamvr/settings")
    async def settings(request: Request):
        try:
            return controller.update_settings(await payload(request))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/api/steamvr/frame.bin")
    def frame(request: Request):
        authorize(request)
        return Response(controller.frame(), media_type="application/octet-stream")

    @router.post("/api/steamvr/events")
    async def events(request: Request):
        authorize(request)
        try:
            controller.ingest(await payload(request))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"status": "accepted"}

    @router.get("/api/steamvr/bridge-token")
    def token(request: Request):
        # Do not permit a third-party link to navigate to the secret download.
        if request.headers.get("sec-fetch-site", "").lower() not in {"", "none", "same-origin"}:
            raise HTTPException(403, "download from the local SteamVR console")
        return Response(controller.token + "\n", media_type="text/plain", headers={"Content-Disposition": 'attachment; filename="mikotype-steamvr-token.txt"', "Cache-Control": "no-store"})

    @router.get("/api/steamvr/preview.png")
    def preview():
        return Response(controller.preview(), media_type="image/png")

    @router.get("/api/steamvr/driver-assets.zip")
    def driver_assets():
        return Response(controller.export_assets(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="mikotype-steamvr-assets.zip"'})

    @router.post("/api/steamvr/collect-logs")
    def collect_logs():
        return controller.collect_logs()

    @router.get("/api/steamvr/diagnostics")
    def diagnostics():
        return Response(json.dumps(controller.status(), ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="mikotype-steamvr-diagnostics.json"'})

    @router.post("/api/steamvr/feedback")
    async def feedback(request: Request):
        value = await payload(request)
        if set(value) != {"message"} or not isinstance(value["message"], str) or not 1 <= len(value["message"].strip()) <= 2000:
            raise HTTPException(422, "feedback message must contain 1-2000 characters")
        controller.add_event("webui", "info", "headset_feedback", value["message"].strip())
        return {"status": "accepted"}

    return router
