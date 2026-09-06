"""Static safety and packaging checks for the Windows-only OpenVR smoke code."""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest


pytestmark = pytest.mark.unit

REPOSITORY = Path(__file__).resolve().parents[3]
MODULE = REPOSITORY / "demo" / "steamvr_home_hybrid"
DRIVER = MODULE / "windows_driver"
DRIVER_PACKAGE = DRIVER / "deskvisionkeyboard"
OVERLAY = MODULE / "windows_overlay"
PACKAGING = MODULE / "packaging"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_driver_manifest_and_package_root_are_exact() -> None:
    assert DRIVER_PACKAGE.name == "deskvisionkeyboard"
    manifests = tuple(DRIVER_PACKAGE.glob("*.vrdrivermanifest"))
    assert manifests == (DRIVER_PACKAGE / "driver.vrdrivermanifest",)
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest == {
        "alwaysActivate": True,
        "name": "deskvisionkeyboard",
        "directory": "",
        "resourceOnly": False,
        "hmd_presence": [],
    }

    settings = json.loads(
        (
            DRIVER_PACKAGE
            / "resources"
            / "settings"
            / "default.vrsettings"
        ).read_text(encoding="utf-8")
    )
    assert settings == {"driver_deskvisionkeyboard": {"enable": True}}


def test_driver_cmake_is_windows_x64_external_sdk_only() -> None:
    cmake = _text(DRIVER / "CMakeLists.txt")
    assert "if(NOT WIN32)" in cmake
    assert "OPENVR_SDK_ROOT" in cmake
    assert "headers/openvr_driver.h" in cmake
    assert "lib/win64/openvr_api.lib" in cmake
    assert 'OUTPUT_NAME "driver_deskvisionkeyboard"' in cmake
    assert 'RUNTIME DESTINATION "deskvisionkeyboard/bin/win64"' in cmake
    assert "FetchContent" not in cmake
    assert "ExternalProject" not in cmake


def test_driver_is_a_minimal_generic_tracker_with_fixed_hmd_relative_pose() -> None:
    source = _text(DRIVER / "src" / "driver.cpp")
    includes = set(re.findall(r"^#include\s+[<\"]([^>\"]+)", source, re.MULTILINE))

    assert "openvr_driver.h" in includes
    assert "openvr.h" not in includes
    assert "vr::TrackedDeviceClass_GenericTracker" in source
    assert '"{deskvisionkeyboard}deskvision_keyboard"' in source
    assert '"DESKVISION-KEYBOARD-DEMO-001"' in source
    assert "GetRawTrackedDevicePoses" in source
    assert "TrackedDevicePoseUpdated" in source
    assert "kOffsetForwardMetres = -0.62" in source
    assert "VR_INIT_SERVER_DRIVER_CONTEXT" in source
    assert "HmdDriverFactory" in source

    lowered = source.casefold()
    assert "#include <openvr.h>" not in lowered
    assert "fastapi" not in lowered
    assert "websocket" not in lowered
    assert "winhttp" not in lowered
    assert "curl" not in lowered
    assert "nlohmann" not in lowered


def test_overlay_is_a_separate_application_and_binds_png_to_tracker() -> None:
    source = _text(OVERLAY / "src" / "main.cpp")
    cmake = _text(OVERLAY / "CMakeLists.txt")

    assert "#include <openvr.h>" in source
    assert "openvr_driver.h" not in source
    assert "vr::VRApplication_Overlay" in source
    assert '"DESKVISION-KEYBOARD-DEMO-001"' in source
    assert '"keyboard_highlight_test.png"' in source
    assert "argc >= 2" in source and "argv[1]" in source
    assert "SetOverlayFromFile" in source
    assert "SetOverlayTransformTrackedDeviceRelative" in source
    assert "SetOverlayWidthInMeters" in source
    assert "kOverlayWidthMetres = 0.3485175F" in source
    assert "VROverlayFlags_NoBackside" in source

    assert "if(NOT WIN32)" in cmake
    assert "OPENVR_SDK_ROOT" in cmake
    assert "headers/openvr.h" in cmake
    assert "lib/win64/openvr_api.lib" in cmake
    assert "bin/win64/openvr_api.dll" in cmake
    assert 'RUNTIME DESTINATION "overlay"' in cmake


def test_build_script_stages_strict_driver_and_overlay_outputs() -> None:
    script = _text(PACKAGING / "build.ps1")
    lowered = script.casefold()

    assert "$OpenVrSdkRoot" in script
    assert '"-A", "x64"' in script
    assert script.count('"-G", "Visual Studio 17 2022"') == 2
    assert "openvr_driver.h" in lowered
    assert "openvr_api.lib" in lowered
    assert "deskvision_keyboard.json" in lowered
    assert "export_manifest.json" in lowered
    assert "$assetmanifest.outputs.psobject.properties" in lowered
    assert "get-filehash -algorithm sha256" in lowered
    assert "exported asset sha-256 mismatch" in lowered
    assert "keyboard_highlight_test.png" in lowered
    assert "driver_deskvisionkeyboard.dll" in lowered
    assert "deskvision_keyboard_overlay.exe" in lowered
    assert "cmake" in lowered
    assert '[string]$BuildRoot' not in script
    assert '[string]$OutputRoot' not in script
    assert 'Join-Path $PSScriptRoot ".build"' in script
    assert 'Join-Path $PSScriptRoot "dist"' in script


def test_install_queries_finddriver_before_any_adddriver() -> None:
    script = _text(PACKAGING / "install.ps1").casefold()
    find_command = script.index("& $vrpathreg finddriver $drivername")
    add_command = script.index("& $vrpathreg adddriver $driverroot")

    assert find_command < add_command
    assert "already registered" in script
    assert "multiple registrations" in script
    assert "removedriverswithname" not in script


def test_uninstall_removes_only_the_exact_bundle_path() -> None:
    script = _text(PACKAGING / "uninstall.ps1").casefold()
    find_command = script.index("& $vrpathreg finddriver $drivername")
    remove_command = script.index("& $vrpathreg removedriver $driverroot")

    assert find_command < remove_command
    assert "refusing to remove" in script
    assert "removedriverswithname" not in script
    assert "remove-item" not in script


def test_smoke_requires_running_vrserver_and_executes_overlay() -> None:
    script = _text(PACKAGING / "smoke.ps1").casefold()

    assert "finddriver" in script
    assert 'get-process -name "vrserver"' in script
    assert "deskvision_keyboard_overlay.exe" in script
    assert "keyboard_highlight_test.png" in script
    assert "vrserver.txt" in script
    assert "vrclient_vrcompositor.txt" in script
    assert "vrcompositor.txt" in script
    assert "vrpathreg show" in script
    assert "registered driver path" in script
    assert "normalized-path" in script
