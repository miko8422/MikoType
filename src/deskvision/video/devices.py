"""Explicit local camera discovery; importing this module opens no hardware.

Mac discovery only reads AVFoundation's inventory, using the same video +
muxed device ordering as OpenCV's AVFoundation camera implementation. Windows
has no portable OpenCV inventory API, so an explicit scan uses bounded child
processes and skips any index already owned by this runtime. Device indices
may change after cameras are connected/disconnected: refresh before choosing.
"""

from __future__ import annotations

import ctypes
import json
import platform
import subprocess
import sys

from deskvision.core.config import CameraConfig
from deskvision.core.platform import validate_camera_backend


class CameraDiscoveryError(RuntimeError):
    """Camera inventory could not be read safely on the current host."""


def _macos_camera_devices() -> list[dict[str, object]]:
    """Read names and OpenCV indices without opening any camera session.

    No PyObjC dependency is needed. The small Objective-C bridge calls only
    inventory methods; it never requests permission or starts video capture.
    Ordering follows OpenCV 4.x ``cap_avfoundation_mac.mm``: Video then Muxed.
    """

    ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
    av = ctypes.CDLL("/System/Library/Frameworks/AVFoundation.framework/AVFoundation")
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    pointer = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = pointer
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = pointer

    # Build fixed signatures rather than mutate objc_msgSend's shared argtypes.
    msg = ctypes.CFUNCTYPE(pointer, pointer, pointer)(("objc_msgSend", objc))
    msg_object = ctypes.CFUNCTYPE(pointer, pointer, pointer, pointer)(
        ("objc_msgSend", objc)
    )
    msg_count = ctypes.CFUNCTYPE(ctypes.c_ulong, pointer, pointer)(
        ("objc_msgSend", objc)
    )
    msg_index = ctypes.CFUNCTYPE(pointer, pointer, pointer, ctypes.c_ulong)(
        ("objc_msgSend", objc)
    )
    msg_text = ctypes.CFUNCTYPE(ctypes.c_char_p, pointer, pointer)(
        ("objc_msgSend", objc)
    )

    def selector(name: str) -> int:
        return objc.sel_registerName(name.encode("ascii"))

    pool = msg(msg(objc.objc_getClass(b"NSAutoreleasePool"), selector("alloc")), selector("init"))
    try:
        camera_class = objc.objc_getClass(b"AVCaptureDevice")
        if not camera_class:
            raise CameraDiscoveryError("AVFoundation camera inventory is unavailable")
        devices: list[dict[str, object]] = []
        for media_name in ("AVMediaTypeVideo", "AVMediaTypeMuxed"):
            media_type = pointer.in_dll(av, media_name).value
            inventory = msg_object(camera_class, selector("devicesWithMediaType:"), media_type)
            for offset in range(msg_count(inventory, selector("count"))):
                device = msg_index(inventory, selector("objectAtIndex:"), offset)
                name = msg(device, selector("localizedName"))
                encoded = msg_text(name, selector("UTF8String"))
                index = len(devices)
                label = encoded.decode("utf-8", errors="replace") if encoded else "Camera"
                devices.append({"device_index": index, "label": f"{label} (#{index})"})
        return devices
    finally:
        msg(pool, selector("drain"))


_WINDOWS_PROBE = """
import json
import sys
try:
    import cv2
except ImportError:
    sys.exit(2)
capture = None
try:
    capture = cv2.VideoCapture(int(sys.argv[1]), getattr(cv2, sys.argv[2]))
    print(json.dumps(bool(capture.isOpened())))
finally:
    if capture is not None:
        capture.release()
"""


def _windows_camera_devices(
    config: CameraConfig,
    *,
    active_device_index: int | None,
    max_devices: int,
    probe_timeout_s: float,
) -> list[dict[str, object]]:
    flags = {"any": "CAP_ANY", "msmf": "CAP_MSMF", "dshow": "CAP_DSHOW"}
    flag = flags[config.backend.lower()]
    devices: list[dict[str, object]] = []
    # Also retain a user-selected high index without scanning an unbounded range.
    indices = sorted(set(range(max_devices)) | ({active_device_index} if active_device_index is not None else set()))
    for index in indices:
        if index == active_device_index:
            devices.append({"device_index": index, "label": f"Camera #{index} (active; not probed)"})
            continue
        try:
            result = subprocess.run(
                [sys.executable, "-c", _WINDOWS_PROBE, str(index), flag],
                capture_output=True,
                text=True,
                timeout=probe_timeout_s,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            # subprocess.run kills and waits for the probe before returning.
            continue
        except OSError as exc:
            raise CameraDiscoveryError(f"camera probe could not start: {exc}") from exc
        if result.returncode == 2:
            raise CameraDiscoveryError("OpenCV is unavailable in the active Python environment")
        if result.returncode != 0:
            continue
        try:
            opened = json.loads(result.stdout.strip()) is True
        except json.JSONDecodeError:
            opened = False
        if opened:
            devices.append({"device_index": index, "label": f"Camera #{index} ({config.backend})"})
    return devices


def enumerate_camera_devices(
    config: CameraConfig,
    *,
    active_device_index: int | None = None,
    system: str | None = None,
    max_devices: int = 10,
    probe_timeout_s: float = 1.5,
) -> list[dict[str, object]]:
    """Return selectable local indices after an explicit user discovery action.

    Callers must pass the running camera index on Windows to avoid reopening
    its handle. Discovery never changes the configured camera or saved data.
    Windows scans indices 0..9 by default; manual selection remains available
    for cameras outside that range. A missing result is not proof a device is
    absent: another program, permissions, or a timeout can prevent opening it.
    """

    if isinstance(max_devices, bool) or not isinstance(max_devices, int) or not 1 <= max_devices <= 32:
        raise ValueError("max_devices must be between 1 and 32")
    if not 0 < probe_timeout_s <= 5:
        raise ValueError("probe_timeout_s must be between 0 and 5")
    if active_device_index is not None and (
        isinstance(active_device_index, bool)
        or not isinstance(active_device_index, int)
        or active_device_index < 0
    ):
        raise ValueError("active_device_index must be a non-negative integer")
    detected = (system or platform.system()).casefold()
    try:
        if detected == "darwin":
            validate_camera_backend("macos", config.backend)
            return _macos_camera_devices()
        if detected == "windows":
            validate_camera_backend("windows", config.backend)
            return _windows_camera_devices(
                config,
                active_device_index=active_device_index,
                max_devices=max_devices,
                probe_timeout_s=probe_timeout_s,
            )
    except (OSError, ValueError, AttributeError) as exc:
        raise CameraDiscoveryError(f"camera discovery failed: {exc}") from exc
    raise CameraDiscoveryError(f"camera discovery supports Windows/macOS, not {detected!r}")


__all__ = ["CameraDiscoveryError", "enumerate_camera_devices"]
