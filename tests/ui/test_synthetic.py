"""Synthetic source tests for the isolated visual test environment."""

import pytest

from tests.ui.synthetic import SyntheticFrameSource


pytestmark = pytest.mark.unit


def test_source_produces_valid_bgr_frame_packets() -> None:
    source = SyntheticFrameSource(width=320, height=180, fps=120)
    source.open()
    first = source.read()
    second = source.read()
    source.close()

    assert first.image_bgr.shape == (180, 320, 3)
    assert first.frame_id == 0
    assert second.frame_id == 1
    assert not source.is_open
