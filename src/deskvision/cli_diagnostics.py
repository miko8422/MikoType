"""Read-only install diagnostics, separate from camera/server startup."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, distribution
import json
from pathlib import Path
import sys

from deskvision import __version__
from deskvision.web.binding import SERVICE_SCHEMA_VERSION


def runtime_identity() -> dict[str, object]:
    source_path = Path(__file__).resolve().with_name("main.py")
    checkout_source = (Path.cwd() / "src" / "deskvision" / "main.py").resolve()
    checkout_has_source = checkout_source.is_file()
    installed_version: str | None = None
    install_origin: object = None
    try:
        package = distribution("vr-desk-vision")
        installed_version = package.version
        raw_origin = package.read_text("direct_url.json")
        if raw_origin:
            install_origin = json.loads(raw_origin)
    except (PackageNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        pass
    return {
        "package_version": __version__,
        "distribution_version": installed_version,
        "service_schema": SERVICE_SCHEMA_VERSION,
        "python_executable": sys.executable,
        "runtime_source": str(source_path),
        "working_directory": str(Path.cwd().resolve()),
        "expected_checkout_source": str(checkout_source) if checkout_has_source else None,
        "source_matches_current_checkout": (
            source_path == checkout_source if checkout_has_source else None
        ),
        "install_origin": install_origin,
    }


def print_version() -> int:
    identity = runtime_identity()
    print(
        f"MikoType {identity['package_version']} "
        f"({identity['service_schema']})\n"
        f"Python: {identity['python_executable']}\n"
        f"Source: {identity['runtime_source']}"
    )
    return 0


def doctor(config_path: Path) -> int:
    identity = runtime_identity()
    resolved_config = config_path.expanduser().resolve()
    checks = {
        "config_exists": resolved_config.is_file(),
        "source_matches_current_checkout": identity["source_matches_current_checkout"],
        "distribution_matches_source": identity["distribution_version"] in (
            None, identity["package_version"]
        ),
    }
    repairs = []
    if not checks["config_exists"]:
        repairs.append(f"Select an existing configuration with --config (missing: {resolved_config})")
    if checks["source_matches_current_checkout"] is False:
        repairs.append("Run this checkout directly: python .\\run_mikotype.py doctor")
    if not checks["distribution_matches_source"] or checks["source_matches_current_checkout"] is False:
        repairs.append("Refresh the editable install: python -m pip install --force-reinstall --no-deps -e .")
    print(
        json.dumps(
            {
                "status": "attention_required" if repairs else "ready",
                "scope": "installation only; camera and service ports are not tested",
                **identity,
                "config_path": str(resolved_config),
                "checks": checks,
                "repair": "\n".join(repairs) if repairs else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2 if repairs else 0
