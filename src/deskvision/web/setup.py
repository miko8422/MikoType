"""HTTP control plane for explicit keyboard setup mode."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response

from deskvision.calibration.control_plane import (
    KeyboardSetupController,
    SetupUnavailableError,
)
from deskvision.web.app import STATIC_DIR


def create_setup_router(controller: KeyboardSetupController) -> APIRouter:
    router = APIRouter()

    def run(action):
        try:
            return action()
        except SetupUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"keyboard setup workspace is unavailable: {exc}",
            ) from exc

    @router.get("/setup", response_class=HTMLResponse)
    def setup_page() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "setup.html").read_text(encoding="utf-8"))

    @router.get("/api/setup/layout")
    def get_layout():
        return run(controller.layout_state)

    @router.put("/api/setup/layout")
    def save_layout(payload: Mapping[str, object]):
        return run(lambda: controller.save_layout(payload))

    @router.post("/api/setup/anchor/start")
    def start_anchor(payload: Mapping[str, object] | None = None):
        reset = bool((payload or {}).get("reset", False))
        return run(lambda: controller.start_anchor_registration(reset=reset))

    @router.post("/api/setup/anchor/observe")
    def observe_anchor():
        return run(controller.observe_anchors)

    @router.post("/api/setup/anchor/finalize")
    def finalize_anchor():
        return run(controller.finalize_anchor)

    @router.get("/api/setup/marker/{marker_id}.png")
    def marker(marker_id: int, size: int = 512) -> Response:
        return Response(
            run(lambda: controller.marker_png(marker_id, size_px=size)),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @router.post("/api/setup/contact/start")
    def start_contact():
        return run(controller.start_contact_calibration)

    @router.get("/api/setup/contact")
    def contact_state():
        return run(controller.contact_state)

    @router.post("/api/setup/contact/capture")
    def capture_contact(payload: Mapping[str, object] | None = None):
        raw_key_id = (payload or {}).get("key_id")
        if raw_key_id is not None and not isinstance(raw_key_id, str):
            raise HTTPException(status_code=422, detail="key_id must be a string")
        return run(lambda: controller.capture_contact(key_id=raw_key_id))

    @router.post("/api/setup/contact/undo")
    def undo_contact():
        return run(controller.undo_contact)

    @router.post("/api/setup/contact/pause")
    def pause_contact(payload: Mapping[str, object]):
        paused = payload.get("paused")
        if not isinstance(paused, bool):
            raise HTTPException(status_code=422, detail="paused must be boolean")
        return run(lambda: controller.pause_contact(paused))

    @router.post("/api/setup/contact/reset")
    def reset_contact():
        return run(controller.reset_contact_draft)

    @router.post("/api/setup/apply")
    def apply_bundle():
        return run(controller.apply_bundle)

    return router


__all__ = ["create_setup_router"]
