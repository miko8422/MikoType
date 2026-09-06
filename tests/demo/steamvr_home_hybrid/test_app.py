"""End-to-end API checks for the camera-free SteamVR hybrid lab."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import zipfile

from fastapi.testclient import TestClient
import pytest

from demo.steamvr_home_hybrid.app import HybridDemoRuntime, create_app
from demo.steamvr_home_hybrid.source_bundle import BUNDLE_ROOT_NAME


pytestmark = pytest.mark.integration

REPOSITORY = Path(__file__).resolve().parents[3]
MODULE = REPOSITORY / "demo" / "steamvr_home_hybrid"
KEYBOARD = REPOSITORY / "data" / "keyboards" / "kzzi_user_adjustable_82"


def _runtime(tmp_path: Path) -> HybridDemoRuntime:
    return HybridDemoRuntime(
        model_path=KEYBOARD / "adaptive_keyboard.glb",
        manifest_path=KEYBOARD / "adaptive_keyboard_manifest.json",
        output_directory=tmp_path / "isolated-output",
        module_directory=MODULE,
    )


@pytest.mark.parametrize(
    "unsafe_output",
    (
        KEYBOARD,
        MODULE / "packaging" / "runtime-output",
        REPOSITORY / "src" / "deskvision" / "runtime-output",
    ),
)
def test_runtime_rejects_outputs_inside_production_or_source_trees(
    unsafe_output: Path,
) -> None:
    with pytest.raises(ValueError, match="outputs|output directory"):
        HybridDemoRuntime(
            model_path=KEYBOARD / "adaptive_keyboard.glb",
            manifest_path=KEYBOARD / "adaptive_keyboard_manifest.json",
            output_directory=unsafe_output,
            module_directory=MODULE,
        )


def test_status_is_truthful_about_offline_mock_and_source_asset(tmp_path: Path) -> None:
    with TestClient(create_app(_runtime(tmp_path))) as client:
        response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["claim"] == "offline-asset-preview-not-steamvr-home-verification"
    assert payload["source"]["key_count"] == 82
    assert payload["source"]["material_count"] == 5
    assert payload["source"]["vertex_count"] == 1992
    assert payload["source"]["triangle_count"] == 996
    assert payload["capabilities"]["asset_export"]["available"] is True
    assert payload["capabilities"]["state_replay"]["available"] is True
    assert payload["capabilities"]["mock_compositor"]["available"] is True
    assert payload["export_current"] is False
    assert payload["windows_source_bundle_current"] is False
    assert next(
        gate for gate in payload["gates"] if gate["id"] == "home_model_visible"
    )["status"] == "blocked"


def test_model_endpoints_serve_the_revision_matched_production_glb(tmp_path: Path) -> None:
    expected_manifest = json.loads(
        (KEYBOARD / "adaptive_keyboard_manifest.json").read_text(encoding="utf-8")
    )
    expected_glb = (KEYBOARD / "adaptive_keyboard.glb").read_bytes()
    with TestClient(create_app(_runtime(tmp_path))) as client:
        manifest_response = client.get("/api/model/manifest")
        model_response = client.get("/api/model/keyboard.glb")

    assert manifest_response.status_code == 200
    assert manifest_response.json() == expected_manifest
    assert model_response.status_code == 200
    assert model_response.headers["content-type"].startswith("model/gltf-binary")
    assert model_response.headers["x-model-revision"] == expected_manifest["model_revision"]
    assert model_response.headers["etag"] == f'"{expected_manifest["model"]["sha256"]}"'
    assert model_response.content == expected_glb


def test_export_builds_current_manifest_and_safe_windows_source_zip(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    with TestClient(create_app(runtime)) as client:
        assert client.get("/api/export/manifest").status_code == 404
        assert client.get("/api/windows-bundle").status_code == 404

        exported = client.post("/api/export")
        manifest_response = client.get("/api/export/manifest")
        bundle_response = client.get("/api/windows-bundle")
        status = client.get("/api/status").json()

    assert exported.status_code == 200
    result = exported.json()
    assert result["claim"] == "source-only-not-built-not-steamvr-verified"
    assert result["gates"]["asset_bundle_validated"] is True
    assert result["gates"]["windows_source_bundle_validated"] is True
    assert result["gates"]["windows_driver_built"] is False
    assert result["gates"]["home_model_visible"] is False
    assert manifest_response.status_code == 200
    export_manifest = manifest_response.json()
    assert export_manifest["render_model"]["filename"] == "deskvision_keyboard.json"
    assert bundle_response.status_code == 200
    assert bundle_response.headers["content-type"].startswith("application/zip")
    assert status["export_current"] is True
    assert status["windows_source_bundle_current"] is True

    bundle = runtime.bundle_path
    assert hashlib.sha256(bundle_response.content).hexdigest() == result["bundle"]["sha256"]
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert len(names) == len(set(names))
        for name in names:
            member = PurePosixPath(name)
            assert not member.is_absolute()
            assert ".." not in member.parts
            assert member.parts[0] == BUNDLE_ROOT_NAME

        prefix = f"{BUNDLE_ROOT_NAME}/"
        required = {
            f"{prefix}SOURCE_BUNDLE_STATUS.json",
            f"{prefix}SHA256SUMS.json",
            f"{prefix}asset_export/export_manifest.json",
            f"{prefix}windows_driver/deskvisionkeyboard/driver.vrdrivermanifest",
            f"{prefix}windows_driver/deskvisionkeyboard/resources/rendermodels/deskvision_keyboard/deskvision_keyboard.json",
            f"{prefix}windows_overlay/assets/keyboard_highlight_test.png",
            f"{prefix}packaging/build.ps1",
            f"{prefix}packaging/install.ps1",
            f"{prefix}packaging/smoke.ps1",
            f"{prefix}packaging/uninstall.ps1",
        }
        assert required <= set(names)
        bundle_status = json.loads(
            archive.read(f"{prefix}SOURCE_BUNDLE_STATUS.json")
        )
        assert bundle_status["claim"] == "windows-source-only-not-built-not-steamvr-verified"
        assert bundle_status["dynamic_scene_state_bridge_included"] is False


def test_repeated_export_is_byte_for_byte_deterministic(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    with TestClient(create_app(runtime)) as client:
        assert client.post("/api/export").status_code == 200
        first = runtime.bundle_path.read_bytes()
        assert client.post("/api/export").status_code == 200
        second = runtime.bundle_path.read_bytes()
    assert first == second


def test_tampered_bundle_is_never_advertised_or_downloaded(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    with TestClient(create_app(runtime)) as client:
        assert client.post("/api/export").status_code == 200
        with zipfile.ZipFile(runtime.bundle_path, mode="a") as archive:
            archive.writestr(f"{BUNDLE_ROOT_NAME}/unexpected.txt", b"tampered")

        status = client.get("/api/status").json()
        bundle = client.get("/api/windows-bundle")

    assert status["windows_source_bundle_current"] is False
    assert bundle.status_code == 404


def test_source_change_invalidates_previously_generated_bundle(tmp_path: Path) -> None:
    module_copy = tmp_path / "module"
    module_copy.mkdir()
    shutil.copy2(MODULE / "README.md", module_copy / "README.md")
    for directory in ("windows_driver", "windows_overlay", "packaging"):
        shutil.copytree(MODULE / directory, module_copy / directory)
    runtime = HybridDemoRuntime(
        model_path=KEYBOARD / "adaptive_keyboard.glb",
        manifest_path=KEYBOARD / "adaptive_keyboard_manifest.json",
        output_directory=tmp_path / "isolated-output",
        module_directory=module_copy,
    )

    with TestClient(create_app(runtime)) as client:
        assert client.post("/api/export").status_code == 200
        assert client.get("/api/status").json()["windows_source_bundle_current"] is True

        smoke = module_copy / "packaging" / "smoke.ps1"
        smoke.write_text(smoke.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")

        status = client.get("/api/status").json()
        bundle = client.get("/api/windows-bundle")

    assert status["windows_source_bundle_current"] is False
    assert bundle.status_code == 404


def test_exporter_contract_change_invalidates_old_export_and_bundle(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    with TestClient(create_app(runtime)) as client:
        assert client.post("/api/export").status_code == 200
        export_manifest_path = runtime.export_manifest_path()
        export_manifest = json.loads(export_manifest_path.read_text(encoding="utf-8"))
        export_manifest["highlight_test"]["width"] = 64
        export_manifest["highlight_test"]["height"] = 64
        export_manifest_path.write_text(
            json.dumps(export_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

        status = client.get("/api/status").json()
        bundle = client.get("/api/windows-bundle")

    assert status["export_current"] is False
    assert status["windows_source_bundle_current"] is False
    assert bundle.status_code == 404


def test_webui_has_no_upload_and_does_not_claim_home_is_verified(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_runtime(tmp_path))) as client:
        page = client.get("/")
        script = client.get("/static/app.js")
        viewer = client.get("/static/vendor/keyboard-3d-viewer.js")

    assert page.status_code == 200
    assert script.status_code == 200
    assert viewer.status_code == 200
    html = page.text
    assert 'type="file"' not in html
    assert "不是 SteamVR Home 的运行截图" in html
    assert "本机离线合成预览" in html
    assert "POST /api/export" not in html  # API prose lives in README, not a fake result.
    assert "/api/windows-bundle" in html
