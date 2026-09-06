from __future__ import annotations

from pathlib import Path

import pytest

from deskvision.main import main


pytestmark = pytest.mark.unit


def test_check_rejects_configuration_that_v01_runtime_cannot_start(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "windows.yaml"
    config.write_text(
        "remote_inference:\n"
        "  enabled: true\n"
        "  endpoint: wss://inference.example.test/ws/experimental/inference\n",
        encoding="utf-8",
    )

    assert main(["check", "--config", str(config)]) == 2
    error = capsys.readouterr().err
    assert "RuntimeBuildError" in error
    assert "not active in V0.1" in error
