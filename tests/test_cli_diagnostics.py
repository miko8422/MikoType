from __future__ import annotations

import json
from pathlib import Path
import tomllib

import pytest

from deskvision import __version__
from deskvision.main import main
from deskvision.web.binding import SERVICE_SCHEMA_VERSION


pytestmark = pytest.mark.unit


def test_package_and_project_versions_match() -> None:
    repository = Path(__file__).resolve().parents[1]
    project = tomllib.loads(
        (repository / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert project["project"]["version"] == __version__


def test_version_reports_loaded_python_and_source(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--version"]) == 0

    output = capsys.readouterr().out
    assert f"MikoType {__version__}" in output
    assert SERVICE_SCHEMA_VERSION in output
    assert "Python:" in output
    assert "Source:" in output


def test_doctor_reports_stale_checkout_without_touching_hardware(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "windows.yaml"
    config.write_text("app:\n  port: 8765\n", encoding="utf-8")
    monkeypatch.setattr(
        "deskvision.main._runtime_identity",
        lambda: {
            "package_version": __version__,
            "distribution_version": "0.1.0.dev0",
            "service_schema": SERVICE_SCHEMA_VERSION,
            "python_executable": "python.exe",
            "runtime_source": r"C:\\old\\src\\deskvision\\main.py",
            "working_directory": str(tmp_path),
            "expected_checkout_source": str(
                tmp_path / "src" / "deskvision" / "main.py"
            ),
            "source_matches_current_checkout": False,
            "install_origin": None,
        },
    )
    monkeypatch.setattr(
        "deskvision.main.require_windows_runtime",
        lambda: pytest.fail("doctor must not initialize the Windows runtime"),
    )

    assert main(["doctor", "--config", str(config)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "attention_required"
    assert payload["checks"]["source_matches_current_checkout"] is False
    assert "force-reinstall" in payload["repair"]
