"""Explicit real-camera pytest entry points; both are skipped by default."""

import pytest

from tests.video.scripts.camera_smoke import run_smoke_test
from tests.video.scripts.memory_stability import run_memory_stability


@pytest.mark.hardware
def test_real_camera_smoke(request: pytest.FixtureRequest) -> None:
    device_index = request.config.getoption("--camera-device-index")
    assert run_smoke_test(seconds=5.0, device_index=device_index) == 0


@pytest.mark.hardware
@pytest.mark.soak
def test_real_camera_memory_stability(request: pytest.FixtureRequest) -> None:
    device_index = request.config.getoption("--camera-device-index")
    duration_s = request.config.getoption("--soak-duration-seconds")
    warmup_s = request.config.getoption("--soak-warmup-seconds")
    post_warmup_s = max(0.0, duration_s - warmup_s)
    sample_interval_s = min(5.0, post_warmup_s / 5.0)
    report_interval_s = min(60.0, max(0.25, duration_s / 2.0))
    assert (
        run_memory_stability(
            duration_s=duration_s,
            warmup_s=warmup_s,
            sample_interval_s=sample_interval_s,
            report_interval_s=report_interval_s,
            device_index=device_index,
        )
        == 0
    )
