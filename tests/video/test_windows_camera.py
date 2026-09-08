"""Unit tests for WindowsCameraSource using an injected OpenCV-like backend."""

import pytest

from deskvision.core.config import CameraConfig
from deskvision.video.windows_camera import CameraOpenError, WindowsCameraSource
from deskvision.video.opencv_camera import CameraSourceError, OpenCVCameraSource


pytestmark = pytest.mark.unit


class FakeImage:
    shape = (2, 3, 3)


class FakeCapture:
    def __init__(
        self,
        opened: bool = True,
        reads: list[tuple[bool, object | None]] | None = None,
    ) -> None:
        self.opened = opened
        self.released = False
        self.settings: list[tuple[int, int]] = []
        self.reads = list(reads or [(True, FakeImage()), (True, FakeImage())])

    def isOpened(self) -> bool:
        return self.opened and not self.released

    def set(self, prop: int, value: int) -> bool:
        self.settings.append((prop, value))
        return True

    def read(self) -> tuple[bool, object | None]:
        if not self.reads:
            return False, None
        return self.reads.pop(0)

    def release(self) -> None:
        self.released = True


class FakeCV2:
    CAP_ANY = 0
    CAP_MSMF = 1400
    CAP_DSHOW = 700
    CAP_AVFOUNDATION = 1200
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5


def test_open_read_close_and_monotonic_frame_ids() -> None:
    captures: list[FakeCapture] = []

    def factory(device_index: int, backend: int) -> FakeCapture:
        assert device_index == 0
        assert backend == FakeCV2.CAP_MSMF
        capture = FakeCapture()
        captures.append(capture)
        return capture

    source = WindowsCameraSource(
        CameraConfig(backend="msmf", width=640, height=480, fps=30),
        capture_factory=factory,
        cv2_module=FakeCV2(),
        clock_ns=iter([100, 200]).__next__,
    )
    source.open()
    first = source.read()
    second = source.read()

    assert source.is_open
    assert first is not None
    assert second is not None
    assert first.frame_id == 0
    assert second.frame_id == 1
    assert captures[0].settings[0][1] == 640
    assert captures[0].settings[1][1] == 480
    source.close()
    source.close()
    assert not source.is_open
    assert captures[0].released


@pytest.mark.parametrize(
    ("backend", "expected"),
    (("any", FakeCV2.CAP_ANY), ("msmf", FakeCV2.CAP_MSMF), ("dshow", FakeCV2.CAP_DSHOW)),
)
def test_windows_backend_mapping(backend: str, expected: int) -> None:
    observed: list[int] = []

    def factory(_device_index: int, flag: int) -> FakeCapture:
        observed.append(flag)
        return FakeCapture()

    source = WindowsCameraSource(
        CameraConfig(backend=backend),
        capture_factory=factory,
        cv2_module=FakeCV2(),
    )
    source.open()
    source.close()

    assert observed == [expected]


def test_failed_open_is_explicit_and_releases_handle() -> None:
    capture = FakeCapture(opened=False)
    source = WindowsCameraSource(
        capture_factory=lambda _device, _backend: capture,
        cv2_module=FakeCV2(),
    )
    with pytest.raises(CameraOpenError):
        source.open()
    assert capture.released
    assert source.last_error is not None


def test_empty_read_returns_none_and_records_error() -> None:
    capture = FakeCapture(reads=[(False, None)])
    source = WindowsCameraSource(
        capture_factory=lambda _device, _backend: capture,
        cv2_module=FakeCV2(),
    )
    source.open()
    assert source.read() is None
    assert source.last_error == "camera read returned no frame"


def test_missing_backend_is_explicit() -> None:
    source = WindowsCameraSource(
        CameraConfig(backend="msmf"),
        capture_factory=lambda _device, _backend: FakeCapture(),
        cv2_module=object(),
    )
    with pytest.raises(CameraOpenError):
        source.open()
    assert "msmf" in (source.last_error or "")


def test_legacy_windows_camera_name_reuses_shared_implementation() -> None:
    assert WindowsCameraSource is OpenCVCameraSource


def test_macos_camera_backend_and_actionable_open_error() -> None:
    observed = []

    def factory(index: int, backend: int) -> FakeCapture:
        observed.append((index, backend))
        return FakeCapture(opened=False)

    source = OpenCVCameraSource(
        CameraConfig(backend="avfoundation", device_index=2),
        capture_factory=factory,
        cv2_module=FakeCV2(),
    )
    with pytest.raises(CameraOpenError, match="Privacy & Security"):
        source.open()
    assert observed == [(2, FakeCV2.CAP_AVFOUNDATION)]


def test_reconfigure_requires_closed_camera_and_preserves_frame_sequence() -> None:
    source = OpenCVCameraSource(
        capture_factory=lambda _index, _backend: FakeCapture(),
        cv2_module=FakeCV2(),
    )
    source.open()
    before = source.read()
    with pytest.raises(CameraSourceError, match="close the camera"):
        source.configure(CameraConfig(device_index=1))
    source.close()
    source.configure(CameraConfig(device_index=1, source_id="second"))
    source.open()
    after = source.read()
    source.close()
    assert before is not None and after is not None
    assert after.source_id == "second"
    assert after.frame_id > before.frame_id
