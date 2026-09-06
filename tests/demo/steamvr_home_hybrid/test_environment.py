from __future__ import annotations

from pathlib import Path

import pytest

from demo.steamvr_home_hybrid.environment import probe_environment


pytestmark = pytest.mark.unit


def test_non_windows_probe_reports_only_portable_offline_capabilities(tmp_path: Path) -> None:
    fake_runtime = tmp_path / "SteamVR" / "bin" / "win64"
    fake_runtime.mkdir(parents=True)
    (fake_runtime / "vrpathreg.exe").touch()
    (fake_runtime / "vrserver.exe").touch()

    capabilities = probe_environment(
        platform_system="Darwin",
        machine="arm64",
        steamvr_root=fake_runtime.parents[1],
        which=lambda _name: "/pretend/tool",
        environ={},
    )

    assert capabilities.asset_export is True
    assert capabilities.state_replay is True
    assert capabilities.mock_compositor is True
    assert capabilities.driver_build is False
    assert capabilities.steamvr_runtime is False
    assert capabilities.steamvr_root is None
    assert capabilities.build_tools == ()
    assert any("not launched" in note for note in capabilities.notes)
    assert capabilities.to_dict()["platform_system"] == "Darwin"


def test_windows_x64_probe_requires_tools_and_both_runtime_files(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "SteamVR"
    win64 = runtime_root / "bin" / "win64"
    win64.mkdir(parents=True)
    (win64 / "vrpathreg.exe").touch()
    (win64 / "vrserver.exe").touch()

    tools = {"cmake": "C:/tools/cmake.exe", "cl": "C:/tools/cl.exe"}
    capabilities = probe_environment(
        platform_system="Windows",
        machine="AMD64",
        steamvr_root=runtime_root,
        which=tools.get,
        environ={},
    )

    assert capabilities.driver_build is True
    assert capabilities.steamvr_runtime is True
    assert capabilities.steamvr_root == str(runtime_root)
    assert capabilities.build_tools == ("cmake", "cl")


def test_windows_probe_does_not_claim_partial_build_or_runtime(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "SteamVR"
    win64 = runtime_root / "bin" / "win64"
    win64.mkdir(parents=True)
    (win64 / "vrpathreg.exe").touch()

    capabilities = probe_environment(
        platform_system="Windows",
        machine="x86_64",
        steamvr_root=runtime_root,
        which=lambda name: "C:/cmake.exe" if name == "cmake" else None,
        environ={},
    )

    assert capabilities.driver_build is False
    assert capabilities.steamvr_runtime is False
    assert capabilities.steamvr_root is None
    assert capabilities.build_tools == ("cmake",)


def test_windows_arm_is_not_reported_as_supported_even_with_fake_tools(
    tmp_path: Path,
) -> None:
    capabilities = probe_environment(
        platform_system="Windows",
        machine="ARM64",
        steamvr_root=tmp_path,
        which=lambda _name: "C:/pretend.exe",
        environ={},
    )

    assert capabilities.driver_build is False
    assert capabilities.steamvr_runtime is False
    assert capabilities.build_tools == ()
