"""Camera-free WebUI for the SteamVR Home hybrid feasibility boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Mapping, Sequence

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn

from .asset_export import (
    AXIS_POLICY_ID,
    AssetExportError,
    CONVERTER_VERSION,
    EXPORT_MANIFEST_FILENAME,
    EXPORT_SCHEMA_VERSION,
    HIGHLIGHT_FILENAME,
    HIGHLIGHT_HEIGHT,
    HIGHLIGHT_WIDTH,
    RENDER_MODEL_FILENAME,
    export_openvr_assets,
    validate_source_assets,
)
from .environment import EnvironmentCapabilities, probe_environment
from .source_bundle import (
    SourceBundleError,
    build_windows_source_bundle,
    source_bundle_input_fingerprint,
    validate_windows_source_bundle,
)


MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parents[1]
STATIC_DIR = MODULE_DIR / "static"
PRODUCTION_KEYBOARD_DIR = (
    PROJECT_ROOT / "data" / "keyboards" / "kzzi_user_adjustable_82"
)
DEFAULT_MODEL_PATH = PRODUCTION_KEYBOARD_DIR / "adaptive_keyboard.glb"
DEFAULT_MANIFEST_PATH = (
    PRODUCTION_KEYBOARD_DIR / "adaptive_keyboard_manifest.json"
)
DEFAULT_OUTPUT_DIR = MODULE_DIR / "output"
DEFAULT_BUNDLE_FILENAME = "deskvision_steamvr_home_windows_source.zip"


class HybridDemoRuntime:
    """Own the immutable source paths and isolated generated outputs."""

    def __init__(
        self,
        *,
        model_path: str | Path = DEFAULT_MODEL_PATH,
        manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
        output_directory: str | Path = DEFAULT_OUTPUT_DIR,
        module_directory: str | Path = MODULE_DIR,
    ) -> None:
        self.model_path = Path(model_path).resolve()
        self.manifest_path = Path(manifest_path).resolve()
        self.output_directory = Path(output_directory).resolve()
        self.module_directory = Path(module_directory).resolve()
        resolved = {
            self.model_path,
            self.manifest_path,
            self.output_directory,
            self.module_directory,
        }
        if len(resolved) != 4:
            raise ValueError("model, manifest, output, and module paths must differ")
        protected_inputs = (self.model_path, self.manifest_path, self.module_directory)
        if any(
            path == self.output_directory or path.is_relative_to(self.output_directory)
            for path in protected_inputs
        ):
            raise ValueError("output directory cannot contain source or module inputs")
        allowed_module_output = self.module_directory / "output"
        if self.output_directory.is_relative_to(self.model_path.parent) or (
            self.output_directory.is_relative_to(self.module_directory)
            and not self.output_directory.is_relative_to(allowed_module_output)
        ) or (
            self.output_directory.is_relative_to(PROJECT_ROOT)
            and not self.output_directory.is_relative_to(allowed_module_output)
        ):
            raise ValueError("Demo outputs must remain outside production and source trees")
        self._lock = RLock()

    def _manifest(self) -> Mapping[str, object]:
        try:
            decoded = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AssetExportError(f"cannot read active model manifest: {exc}") from exc
        if not isinstance(decoded, dict):
            raise AssetExportError("active model manifest must be a JSON object")
        return decoded

    def source_summary(self) -> dict[str, object]:
        return validate_source_assets(self.model_path, self.manifest_path)

    def _active_export_directory(self, source: Mapping[str, object]) -> Path:
        digest = source.get("glb_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise AssetExportError("validated source summary has no GLB SHA-256")
        return self.output_directory / "openvr_keyboard" / digest[:16]

    @property
    def bundle_path(self) -> Path:
        return self.output_directory / DEFAULT_BUNDLE_FILENAME

    def export_manifest_path(self) -> Path:
        source = self.source_summary()
        return self._active_export_directory(source) / EXPORT_MANIFEST_FILENAME

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def export_is_current(self, source: Mapping[str, object] | None = None) -> bool:
        try:
            source_summary = dict(source or self.source_summary())
            export_directory = self._active_export_directory(source_summary)
            manifest_path = export_directory / EXPORT_MANIFEST_FILENAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                return False
            if (
                manifest.get("schema_version") != EXPORT_SCHEMA_VERSION
                or manifest.get("converter_version") != CONVERTER_VERSION
                or manifest.get("export_manifest")
                != {"filename": EXPORT_MANIFEST_FILENAME}
            ):
                return False
            exported_source = manifest.get("source")
            if not isinstance(exported_source, dict):
                return False
            source_identity_fields = (
                "glb_filename",
                "glb_sha256",
                "glb_byte_length",
                "manifest_filename",
                "manifest_sha256",
                "model_revision",
                "geometry_revision",
                "node_contract",
                "key_count",
            )
            expected_source = {
                field: source_summary.get(field) for field in source_identity_fields
            }
            if exported_source != expected_source:
                return False
            axis_policy = manifest.get("axis_policy")
            render_model = manifest.get("render_model")
            highlight_test = manifest.get("highlight_test")
            if (
                not isinstance(axis_policy, dict)
                or axis_policy.get("id") != AXIS_POLICY_ID
                or not isinstance(render_model, dict)
                or render_model.get("name") != "deskvision_keyboard"
                or render_model.get("filename") != RENDER_MODEL_FILENAME
                or not isinstance(highlight_test, dict)
                or highlight_test.get("filename") != HIGHLIGHT_FILENAME
                or highlight_test.get("width") != HIGHLIGHT_WIDTH
                or highlight_test.get("height") != HIGHLIGHT_HEIGHT
            ):
                return False
            outputs = manifest.get("outputs")
            if not isinstance(outputs, dict) or not outputs:
                return False
            actual_names = {
                path.name
                for path in export_directory.iterdir()
                if path.is_file() and not path.is_symlink()
            }
            if actual_names != set(outputs) | {EXPORT_MANIFEST_FILENAME}:
                return False
            for filename, record in outputs.items():
                if (
                    not isinstance(filename, str)
                    or Path(filename).name != filename
                    or not isinstance(record, dict)
                ):
                    return False
                path = export_directory / filename
                expected_hash = record.get("sha256")
                expected_length = record.get("byte_length")
                if (
                    not path.is_file()
                    or path.stat().st_size != expected_length
                    or self._file_sha256(path) != expected_hash
                ):
                    return False
            return True
        except (AssetExportError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False

    def bundle_is_current(self, source: Mapping[str, object] | None = None) -> bool:
        if not self.export_is_current(source) or not self.bundle_path.is_file():
            return False
        try:
            export_manifest = self.export_manifest_path()
            if self.bundle_path.stat().st_mtime_ns < export_manifest.stat().st_mtime_ns:
                return False
            bundle = validate_windows_source_bundle(self.bundle_path)
            expected_fingerprint = source_bundle_input_fingerprint(
                module_directory=self.module_directory,
                export_directory=export_manifest.parent,
            )
            return bundle.get("input_fingerprint") == expected_fingerprint
        except (AssetExportError, SourceBundleError, OSError):
            return False

    @staticmethod
    def _capability(
        available: bool,
        available_reason: str,
        unavailable_reason: str,
    ) -> dict[str, object]:
        return {
            "available": bool(available),
            "reason": available_reason if available else unavailable_reason,
        }

    def status(
        self,
        environment: EnvironmentCapabilities | None = None,
    ) -> dict[str, object]:
        capability = environment or probe_environment()
        source_error: str | None = None
        try:
            source = self.source_summary()
        except AssetExportError as exc:
            source = {}
            source_error = str(exc)
        source_ready = source_error is None
        export_ready = source_ready and self.export_is_current(source)
        bundle_ready = export_ready and self.bundle_is_current(source)

        capabilities = {
            "asset_export": self._capability(
                capability.asset_export and source_ready,
                "GLB/Manifest 可在本机严格校验并转换",
                source_error or "本机不能执行资产转换",
            ),
            "state_replay": self._capability(
                capability.state_replay,
                "SceneState revision/sequence/TTL 可离线回放",
                "本机不能运行状态回放",
            ),
            "mock_compositor": self._capability(
                capability.mock_compositor,
                "浏览器可模拟模型与键帽高亮的组合效果",
                "本机不能运行模拟合成器",
            ),
            "driver_build": self._capability(
                capability.driver_build,
                "Windows x64 构建工具链已发现",
                "需要 Windows x64、CMake 与 MSVC/clang-cl/MSBuild",
            ),
            "steamvr_runtime": self._capability(
                capability.steamvr_runtime,
                "SteamVR Runtime 文件已发现；仍需头显实测",
                "SteamVR Runtime/Home 验收需要 Windows x64",
            ),
        }
        gates = (
            {
                "id": "asset_bundle_validated",
                "status": "passed" if export_ready else ("ready" if source_ready else "blocked"),
                "note": "已验证" if export_ready else ("可执行" if source_ready else "源资产失败"),
            },
            {
                "id": "windows_source_bundle_validated",
                "status": "passed" if bundle_ready else "ready",
                "note": "源码就绪" if bundle_ready else "待生成",
            },
            {
                "id": "windows_driver_built",
                "status": "ready" if capability.driver_build else "blocked",
                "note": "可构建" if capability.driver_build else "需 Windows",
            },
            {
                "id": "steamvr_driver_loaded",
                "status": "ready" if capability.steamvr_runtime else "blocked",
                "note": "待实测" if capability.steamvr_runtime else "需 SteamVR",
            },
            {
                "id": "home_model_visible",
                "status": "blocked",
                "note": "需头显实测",
            },
            {
                "id": "overlay_aligned",
                "status": "blocked",
                "note": "需外参标定",
            },
        )
        return {
            "schema_version": "steamvr-home-hybrid-status-0.1",
            "demo": "steamvr_home_hybrid",
            "mode": "offline-mock" if not capability.steamvr_runtime else "runtime-present-not-verified",
            "claim": "offline-asset-preview-not-steamvr-home-verification",
            "platform": {
                "system": capability.platform_system,
                "machine": capability.machine,
            },
            "capabilities": capabilities,
            "gates": list(gates),
            "source": source,
            "source_error": source_error,
            "export_current": export_ready,
            "windows_source_bundle_current": bundle_ready,
            "environment_notes": list(capability.notes),
        }

    def export(self) -> dict[str, object]:
        with self._lock:
            source = self.source_summary()
            export_directory = self._active_export_directory(source)
            export_manifest = export_openvr_assets(
                self.model_path,
                self.manifest_path,
                export_directory,
            )
            bundle = build_windows_source_bundle(
                module_directory=self.module_directory,
                export_directory=export_directory,
                output_path=self.bundle_path,
            )
            outputs = export_manifest.get("outputs", {})
            return {
                "schema_version": "steamvr-home-hybrid-export-result-0.1",
                "claim": "source-only-not-built-not-steamvr-verified",
                "export_manifest": export_manifest,
                "bundle": bundle,
                "file_count": len(outputs) + int(bundle.get("file_count", 0)),
                "gates": {
                    "asset_bundle_validated": True,
                    "windows_source_bundle_validated": True,
                    "windows_driver_built": False,
                    "steamvr_driver_loaded": False,
                    "home_model_visible": False,
                    "overlay_aligned": False,
                },
            }


def create_app(runtime: HybridDemoRuntime | None = None) -> FastAPI:
    active = runtime or HybridDemoRuntime()
    app = FastAPI(
        title="SteamVR Home Hybrid Feasibility Demo",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/api/status")
    def status() -> dict[str, object]:
        return active.status()

    @app.get("/api/model/manifest")
    def model_manifest() -> Mapping[str, object]:
        try:
            active.source_summary()
            return active._manifest()
        except AssetExportError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/model/keyboard.glb")
    def model_glb() -> FileResponse:
        try:
            source = active.source_summary()
        except AssetExportError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FileResponse(
            active.model_path,
            media_type="model/gltf-binary",
            filename=active.model_path.name,
            headers={
                "ETag": f'"{source["glb_sha256"]}"',
                "X-Model-Revision": str(source["model_revision"]),
                "Cache-Control": "private, no-cache",
            },
        )

    @app.post("/api/export")
    def export() -> dict[str, object]:
        try:
            return active.export()
        except (AssetExportError, SourceBundleError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/export/manifest")
    def export_manifest() -> FileResponse:
        try:
            path = active.export_manifest_path()
        except AssetExportError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not active.export_is_current() or not path.is_file():
            raise HTTPException(status_code=404, detail="no current export manifest")
        return FileResponse(
            path,
            media_type="application/json",
            filename=EXPORT_MANIFEST_FILENAME,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/windows-bundle")
    def windows_bundle() -> FileResponse:
        if not active.bundle_is_current():
            raise HTTPException(
                status_code=404,
                detail="generate and validate the Windows source bundle first",
            )
        return FileResponse(
            active.bundle_path,
            media_type="application/zip",
            filename=active.bundle_path.name,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the isolated SteamVR Home hybrid feasibility WebUI."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8776)
    args = parser.parse_args(argv)
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_BUNDLE_FILENAME",
    "DEFAULT_MANIFEST_PATH",
    "DEFAULT_MODEL_PATH",
    "DEFAULT_OUTPUT_DIR",
    "HybridDemoRuntime",
    "create_app",
    "main",
]
