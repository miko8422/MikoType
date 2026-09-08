from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
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
        "deskvision.cli_diagnostics.runtime_identity",
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


@pytest.mark.integration
def test_checkout_launcher_ignores_stale_import_path_and_external_cwd(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    stale_source = tmp_path / "old-install"
    package = stale_source / "deskvision"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "raise RuntimeError('stale install imported')\n", encoding="utf-8"
    )
    environment = {**os.environ, "PYTHONPATH": str(stale_source)}
    result = subprocess.run(
        [sys.executable, str(repository / "run_mikotype.py"), "--version"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert f"MikoType {__version__}" in result.stdout
    assert str(repository / "src" / "deskvision" / "main.py") in result.stdout
    assert "stale install" not in result.stdout + result.stderr


def test_doctor_missing_config_gives_config_repair(tmp_path: Path, capsys) -> None:
    assert main(["doctor", "--config", str(tmp_path / "missing.yaml")]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["checks"]["config_exists"] is False
    assert "--config" in payload["repair"]
    assert "installation only" in payload["scope"]
