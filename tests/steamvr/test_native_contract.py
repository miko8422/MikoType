"""Portable executable tests of the actual C++ bridge decoder and pose maths.

These do not claim that Windows DLLs compiled or that Home rendered a tracker.
Windows compilation is a separate CI job; headset acceptance remains manual.
"""
from pathlib import Path
import shutil
import struct
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "integrations" / "steamvr"


@pytest.fixture(scope="module")
def native_decoder(tmp_path_factory):
    compiler = shutil.which("clang++") or shutil.which("g++")
    if compiler is None:
        pytest.skip("A C++17 compiler is needed for portable native protocol checks")
    executable = tmp_path_factory.mktemp("native-protocol") / "protocol-check"
    subprocess.run(
        [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(NATIVE / "common"),
         str(Path(__file__).with_name("native_protocol_check.cpp")), "-o", str(executable)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    return executable


def frame_bytes(**changes):
    fields = {"width": 2, "height": 1, "flags": 7, "sequence": 0x100000002,
              "width_m": 0.4, "height_m": 0.2, "x": 0.0, "y": 0.8, "z": -0.6,
              "yaw": 0.0, "pitch": -90.0, "roll": 0.0}
    fields.update(changes)
    return struct.pack(
        "<8sIIIIQ8f32s", b"MIKOVR01", 1, fields["width"], fields["height"], fields["flags"],
        fields["sequence"], *(fields[key] for key in ("width_m", "height_m", "x", "y", "z", "yaw", "pitch", "roll")),
        bytes.fromhex("aa" * 32),
    ) + bytes(8)


def decode(executable, tmp_path, payload):
    source = tmp_path / "frame.bin"
    source.write_bytes(payload)
    return subprocess.run([str(executable), str(source)], capture_output=True, text=True, timeout=5)


def test_binary_layout_and_standing_to_raw(native_decoder, tmp_path):
    payload = frame_bytes()
    assert len(payload) == 96 + 8
    result = decode(native_decoder, tmp_path, payload)
    assert result.returncode == 0, result.stdout + result.stderr
    values = [float(value) for value in result.stdout.split()]
    assert values[:12] == pytest.approx([0, 1, 0, 3.6, 0, 0, 1, -1.2, 1, 0, 0, -1], abs=1e-5)
    assert int(values[12]) == 0x100000002
    assert values[13] == 8


@pytest.mark.parametrize("payload", [
    b"", frame_bytes()[:95], b"WRONGMAG" + frame_bytes()[8:],
    frame_bytes()[:8] + struct.pack("<I", 2) + frame_bytes()[12:],
    frame_bytes(flags=8), frame_bytes(width=0), frame_bytes(height=2049),
    frame_bytes(width=2048, height=2048), frame_bytes() + b"extra",
    frame_bytes(width_m=float("nan")), frame_bytes(height_m=0),
    frame_bytes(x=11), frame_bytes(y=-3), frame_bytes(pitch=181),
])
def test_invalid_native_frames_are_rejected(native_decoder, tmp_path, payload):
    result = decode(native_decoder, tmp_path, payload)
    assert result.returncode == 2, result.stdout + result.stderr


def test_native_model_fingerprint_is_not_a_label(native_decoder, tmp_path):
    payload = bytearray(frame_bytes())
    payload[64] ^= 0xFF
    assert decode(native_decoder, tmp_path, payload).returncode == 3


def test_driver_and_transport_are_isolated_by_bounded_pose_channel():
    driver = (NATIVE / "driver/src/driver.cpp").read_text()
    channel = (NATIVE / "common/pose_channel.h").read_text()
    bridge = (NATIVE / "bridge/src/main.cpp").read_text()
    assert "WinHttp" not in driver
    assert "GetRawTrackedDevicePoses" not in driver  # no old HMD-follow placement
    assert "WaitForSingleObject(mutex_, 0)" in channel
    assert "WINHTTP_ACCESS_TYPE_NO_PROXY" in bridge
    assert "WINHTTP_OPTION_REDIRECT_POLICY_NEVER" in bridge
    assert "model_mismatch" in bridge
    assert "X-MikoType-SteamVR-Token" in bridge
    assert "GetCurrentSceneProcessId" in bridge
    assert "steamtours.exe" in bridge


def test_native_packaging_has_no_broad_deletes_or_demo_dependency():
    for path in (NATIVE / "scripts").glob("*.ps1"):
        text = path.read_text()
        assert "Remove-Item" not in text
        assert "demo.steamvr" not in text
    workflow = (ROOT / ".github/workflows/steamvr-windows.yml").read_text()
    assert "repository: ValveSoftware/openvr" in workflow
    assert "41bc3825fd35b04047610c86fee26fb33b017b29" in workflow
    assert "contents: read" in workflow
