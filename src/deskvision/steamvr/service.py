"""Camera-independent SteamVR control plane and bounded live overlay frames.

This is a manual VR-space anchor, not a metric pose inferred from ArUco.
Fingertips are projected to the keyboard plane; no invented depth is exported.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import secrets
import struct
import sys
import tempfile
from threading import RLock
import time
from zipfile import ZipFile, ZIP_DEFLATED

import cv2
import numpy as np

from deskvision import __version__
from deskvision.state.store import LatestSceneStateStore
from deskvision.steamvr.diagnostics import collect_runtime_logs, redact


SCHEMA = "mikotype-steamvr-0.1"
FRAME_HEADER = struct.Struct("<8sIIIIQ8f32s")
DEFAULT_SETTINGS = {"enabled": False, "pose_confirmed": False, "x": 0.0, "y": 0.75, "z": -0.6, "yaw": 0.0, "pitch": -90.0, "roll": 0.0}
LIMITATIONS = [
    "Mac 可测试状态、贴图、模型导出；SteamVR / Home 和 Pimax 的显示效果必须在 Windows 头显验收。",
    "3D 键盘由 GenericTracker 驱动提交；Home 是否绘制该设备由 Home 决定，接口成功不等于头显可见。",
    "高亮和指尖是键盘表面上的二维 Overlay，不是有真实深度的 3D 手部骨骼，也不代表机械按键触发。",
    "键盘空间位姿需手动对齐，移动实体键盘或重设 SteamVR 房间后必须重新对齐；ArUco 平面定位不等于 VR 六自由度定位。",
    "本地日志只保留最近 300 条；收集 SteamVR 文件日志需手动点击，导出前请检查隐私信息。",
]


class SteamVRController:
    def __init__(self, states: LatestSceneStateStore, manifest: dict, glb: bytes, *, stale_after_ms: int = 1000):
        self.states = states
        self.manifest = deepcopy(manifest)
        self.glb = glb
        self.sha256 = hashlib.sha256(glb).hexdigest()
        if self.sha256 != manifest["model"]["sha256"]:
            raise ValueError("SteamVR model snapshot hash mismatch")
        self.keys = {key["key_id"]: key for key in self.manifest["keys"]}
        self.width_m, _, self.height_m = self.manifest["geometry"]["case_size"]
        self.width = 768
        self.height = max(32, min(2048, round(self.width * self.height_m / self.width_m)))
        self.stale_after_ms = stale_after_ms
        self.token = secrets.token_urlsafe(32)
        self.lock = RLock()
        self.settings = dict(DEFAULT_SETTINGS)
        self.logs: deque[dict] = deque(maxlen=300)
        self.sequence = 0
        self.log_id = 0
        self.last_seen: float | None = None
        self.bridge_status = "not_connected"
        self.bridge_details: dict = {}
        self.runtime_logs: dict = {"status": "not_collected", "files": []}
        self._export_cache: bytes | None = None
        self._render_key = None
        self._render_cache: np.ndarray | None = None
        self._observed_generation = -1
        self._observed_deadline = 0.0
        self._expired = True
        self._reference = np.array([k["contact_center_reference"] for k in self.keys.values()], dtype=float)
        self._centers = np.array([[k["center"][0], k["center"][2]] for k in self.keys.values()], dtype=float)
        design = np.column_stack((self._reference, np.ones(len(self.keys))))
        self._affine, _, self._reference_rank, _ = np.linalg.lstsq(design, self._centers, rcond=None)
        self._residuals = self._centers - design @ self._affine
        self.add_event("service", "info", "service_ready", "SteamVR 观测接口已就绪，等待 Windows bridge。")

    def add_event(self, source: str, level: str, event: str, message: str, details: dict | None = None) -> None:
        with self.lock:
            self.log_id += 1
            self.logs.append({"id": self.log_id, "time": datetime.now(timezone.utc).isoformat(), "source": source, "level": level, "event": event,
                              "message": redact(message, token=self.token)[:1000], "details": self._safe_details(details or {})})

    def _safe_details(self, details: dict) -> dict:
        result = {}
        for key, value in list(details.items())[:20]:
            if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,64}", key):
                continue
            if re.search(r"token|secret|password|api.?key", key, re.I):
                continue
            if isinstance(value, str):
                result[key] = redact(value, token=self.token)[:300]
            elif value is None or isinstance(value, (bool, int)):
                result[key] = value
            elif isinstance(value, float) and math.isfinite(value):
                result[key] = value
        return result

    def ingest(self, payload: dict) -> None:
        if set(payload) - {"source", "level", "event", "message", "details"}:
            raise ValueError("unknown event fields")
        if payload.get("source") != "windows-bridge" or payload.get("level") not in {"info", "warning", "error"}:
            raise ValueError("invalid event source or level")
        event, message, details = payload.get("event"), payload.get("message"), payload.get("details", {})
        if not isinstance(event, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", event):
            raise ValueError("invalid event identifier")
        if not isinstance(message, str) or len(message) > 2000 or not isinstance(details, dict):
            raise ValueError("invalid event message/details")
        with self.lock:
            self.last_seen = time.monotonic()
            if event != "bridge_heartbeat":
                self.bridge_status = event
            elif self.bridge_status == "not_connected":
                self.bridge_status = "connected"
            self.bridge_details = self._safe_details(details)
            # Heartbeats update liveness without replacing all useful logs.
            if event != "bridge_heartbeat":
                self.add_event("windows-bridge", payload["level"], event, message, details)

    def update_settings(self, payload: dict) -> dict:
        if set(payload) != set(DEFAULT_SETTINGS):
            raise ValueError("settings require enabled, pose_confirmed, x, y, z, yaw, pitch, roll")
        clean = {}
        for key, value in payload.items():
            if key in {"enabled", "pose_confirmed"}:
                if type(value) is not bool:
                    raise ValueError(f"{key} must be boolean")
            else:
                low, high = ((-180, 180) if key in {"yaw", "pitch", "roll"} else ((-2, 10) if key == "y" else (-10, 10)))
                if type(value) not in {int, float} or not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f"{key} must be finite in [{low}, {high}]")
                value = float(value)
            clean[key] = value
        with self.lock:
            self.settings = clean
            self.add_event("webui", "info", "alignment_changed", "已更新本次运行的手动空间对齐；服务重启后需要重新确认。", clean)
        return self.status()

    def _scene(self):
        generation, state = self.states.latest_with_generation()
        age = None if state is None else (time.time_ns() - state.captured_at_ns) / 1e6
        now = time.monotonic()
        if generation != self._observed_generation:
            self._observed_generation = generation
            self._expired = age is None or not 0 <= age <= self.stale_after_ms
            self._observed_deadline = now + max(0, self.stale_after_ms - (age or 0)) / 1000
        if now > self._observed_deadline or age is None or not 0 <= age <= self.stale_after_ms:
            self._expired = True
        fresh = state is not None and not self._expired
        keyboard = None if state is None else state.keyboard
        pose_usable = bool(keyboard is not None and keyboard.pose.usable)
        revisions_match = bool(keyboard and keyboard.model.sha256 == self.sha256 and keyboard.artifacts.model == self.manifest["model_revision"]
                               and keyboard.artifacts.contact_map == self.manifest["contact_binding"]["contact_map_revision"]
                               and keyboard.artifacts.anchor == self.manifest["contact_binding"]["anchor_revision"]
                               and keyboard.artifacts.layout_content == self.manifest["layout"]["profile_content_hash"]
                               and keyboard.artifacts.layout_inventory == self.manifest["layout"]["inventory_revision"])
        usable = fresh and pose_usable and revisions_match
        return state, {"generation": generation, "fresh": fresh, "pose_usable": pose_usable, "revisions_match": revisions_match, "overlay_fresh": usable,
                       "age_ms": age, "key_count": len(self.keys), "highlights": len(state.key_highlights) if usable else 0,
                       "fingertips": len(state.fingertips) if usable else 0, "model_revision": self.manifest["model_revision"], "model_sha256": self.sha256}

    def status(self) -> dict:
        with self.lock:
            _, scene = self._scene()
            age = None if self.last_seen is None else (time.monotonic() - self.last_seen) * 1000
            return {"schema_version": SCHEMA, "package_version": __version__, "supported_host": sys.platform == "win32", "platform": sys.platform,
                    "bridge": {"connected": age is not None and age < 6000, "last_seen_age_ms": age, "status": self.bridge_status, "details": deepcopy(self.bridge_details)},
                    "settings": dict(self.settings), "scene": scene, "logs": list(self.logs), "runtime_logs": deepcopy(self.runtime_logs), "limitations": LIMITATIONS}

    def _point(self, x: float, z: float) -> tuple[int, int]:
        return (round((x / self.width_m + .5) * (self.width - 1)), round((z / self.height_m + .5) * (self.height - 1)))

    def project_tip(self, x: float, y: float) -> tuple[float, float] | None:
        """Affine baseline + continuous inverse-distance calibration residuals.

        Using all contacts avoids nearest-neighbour set changes. Residual weights
        approach one at each measured centre, without a special-case jump.
        """
        delta = self._reference - [x, y]
        distance = np.linalg.norm(delta, axis=1)
        if not len(distance) or self._reference_rank < 3:
            return None
        weight = 1 / np.maximum(distance, 1e-10) ** 2
        weight /= weight.sum()
        point = np.array([x, y, 1.0]) @ self._affine + weight @ self._residuals
        if not np.isfinite(point).all() or abs(point[0]) > self.width_m / 2 or abs(point[1]) > self.height_m / 2:
            return None
        return float(point[0]), float(point[1])

    def render(self) -> tuple[np.ndarray, dict]:
        with self.lock:
            state, scene = self._scene()
            cache_key = (scene["generation"], scene["overlay_fresh"])
            if self._render_key == cache_key and self._render_cache is not None:
                return self._render_cache, scene
            image = np.zeros((self.height, self.width, 4), dtype=np.uint8)
            highlights = {h.physical_key_id: h for h in state.key_highlights} if scene["overlay_fresh"] else {}
            for key_id, key in self.keys.items():
                x, _, z = key["center"]
                w, _, h = key["size"]
                p1, p2 = self._point(x - w / 2, z - h / 2), self._point(x + w / 2, z + h / 2)
                # Thin outlines also provide a plane fallback if Home hides trackers.
                cv2.rectangle(image, p1, p2, (130, 175, 200, 95), 1)
                highlight = highlights.get(key_id)
                if highlight:
                    alpha = round(50 + 170 * highlight.intensity)
                    cv2.rectangle(image, p1, p2, (80, 255, 155, alpha) if highlight.direct else (60, 155, 255, alpha), -1)
            if scene["overlay_fresh"]:
                for tip in state.fingertips:
                    point = self.project_tip(tip.reference_x, tip.reference_y)
                    if point is not None:
                        center = self._point(*point)
                        cv2.circle(image, center, 10, (70, 235, 255, 150), -1)
                        cv2.circle(image, center, 4, (220, 255, 255, 255), -1)
            self._render_key, self._render_cache = cache_key, image
            return image, scene

    def frame(self) -> bytes:
        with self.lock:
            image, scene = self.render()
            self.sequence += 1
            flags = int(scene["overlay_fresh"]) | (int(self.settings["enabled"]) << 1) | (int(self.settings["pose_confirmed"]) << 2)
            header = FRAME_HEADER.pack(b"MIKOVR01", 1, self.width, self.height, flags, self.sequence, self.width_m, self.height_m,
                                       *(self.settings[k] for k in ("x", "y", "z", "yaw", "pitch", "roll")), bytes.fromhex(self.sha256))
            # Preview may keep outlines; stale transport must not retain highlights.
            pixels = image.tobytes() if flags & 1 else bytes(image.nbytes)
            return header + pixels

    def preview(self) -> bytes:
        image, _ = self.render()
        ok, encoded = cv2.imencode(".png", cv2.cvtColor(image, cv2.COLOR_RGBA2BGRA))
        if not ok:
            raise RuntimeError("overlay preview encoding failed")
        return encoded.tobytes()

    def export_assets(self) -> bytes:
        from deskvision.steamvr.assets import export_openvr_assets

        with self.lock:
            if self._export_cache is None:
                with tempfile.TemporaryDirectory(prefix="mikotype-steamvr-") as temp:
                    root = Path(temp)
                    (root / "keyboard.glb").write_bytes(self.glb)
                    (root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
                    export_openvr_assets(root / "keyboard.glb", root / "manifest.json", root / "assets")
                    output = io.BytesIO()
                    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
                        for path in sorted((root / "assets").iterdir()):
                            archive.write(path, arcname=path.name)
                    self._export_cache = output.getvalue()
                self.add_event("service", "info", "assets_exported", "已导出当前用户键盘的 OpenVR render model。", {"model_sha256": self.sha256, "key_count": len(self.keys)})
            return self._export_cache

    def collect_logs(self) -> dict:
        result = collect_runtime_logs(token=self.token)
        with self.lock:
            self.runtime_logs = result
            self.add_event("webui", "info", "runtime_logs_collected", "已尝试收集 SteamVR 固定日志文件末尾。", {"status": result["status"]})
        return result
