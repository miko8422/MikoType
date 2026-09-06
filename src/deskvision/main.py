"""Command-line entry point for production runtime and keyboard artifacts."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

import uvicorn

from deskvision.calibration.artifacts import load_keyboard_calibration_artifacts
from deskvision.calibration.control_plane import KeyboardSetupController, SetupWorkspace
from deskvision.core.config import load_config
from deskvision.core.platform import is_loopback_host, require_windows_runtime
from deskvision.keyboard.bundle import KeyboardBundlePaths, build_keyboard_bundle
from deskvision.runtime import (
    build_runtime,
    ensure_keyboard_model,
    validate_runtime_config,
)
from deskvision.web.setup import create_setup_router


DEFAULT_CONFIG = Path("configs/windows.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mikotype",
        description="Windows-local vision runtime for adaptive keyboard mapping",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser(
        "check", help="validate production config, calibration, and adaptive model"
    )
    check.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    run = subparsers.add_parser(
        "run", help="start camera, mapping pipeline, and local inspector"
    )
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    run.add_argument("--host")
    run.add_argument("--port", type=int)
    run.add_argument(
        "--acknowledge-mediapipe-metrics",
        action="store_true",
        help=(
            "explicitly acknowledge MediaPipe Tasks performance/utilization "
            "metrics for this run"
        ),
    )

    setup = subparsers.add_parser(
        "setup", help="start the isolated layout/anchor/contact calibration UI"
    )
    setup.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    setup.add_argument("--host")
    setup.add_argument("--port", type=int)
    setup.add_argument(
        "--workspace",
        type=Path,
        default=Path("data/keyboards/.setup"),
    )
    setup.add_argument(
        "--acknowledge-mediapipe-metrics",
        action="store_true",
        help=(
            "explicitly acknowledge MediaPipe Tasks performance/utilization "
            "metrics for this setup run"
        ),
    )

    bundle = subparsers.add_parser(
        "build-keyboard", help="validate calibration and create a deployable 3D bundle"
    )
    bundle.add_argument("--layout", type=Path, required=True)
    bundle.add_argument("--anchor", type=Path, required=True)
    bundle.add_argument("--contact-map", type=Path, required=True)
    bundle.add_argument("--output", type=Path, required=True)
    return parser


def _check(config_path: Path) -> int:
    config = load_config(config_path)
    validate_runtime_config(config)
    artifacts = load_keyboard_calibration_artifacts(
        layout_path=config.artifacts.layout_profile,
        anchor_path=config.artifacts.anchor_reference,
        contact_map_path=config.artifacts.contact_map,
    )
    generated = ensure_keyboard_model(
        artifacts,
        model_path=config.artifacts.model_glb,
        manifest_path=config.artifacts.model_manifest,
        repair=False,
    )
    print(
        json.dumps(
            {
                "status": "ready",
                "deployment": {
                    "target_os": config.deployment.target_os,
                    "topology": config.deployment.topology,
                    "remote_inference_enabled": config.remote_inference.enabled,
                },
                "layout_id": artifacts.layout.layout_id,
                "key_count": len(artifacts.layout.keys),
                "contact_samples": sum(
                    len(key.samples) for key in artifacts.contact_map.keys
                ),
                "revisions": {
                    **artifacts.revisions,
                    "model": generated.manifest["model_revision"],
                },
                "model_path": str(config.artifacts.model_glb),
                "manifest_path": str(config.artifacts.model_manifest),
                "runtime_note": (
                    "run requires explicit MediaPipe metrics acknowledgement"
                    if not config.hand_tracking.metrics_acknowledged
                    else "MediaPipe metrics acknowledged in config"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _run(args: argparse.Namespace) -> int:
    require_windows_runtime()
    config = load_config(args.config)
    if args.acknowledge_mediapipe_metrics:
        config = replace(
            config,
            hand_tracking=replace(
                config.hand_tracking,
                metrics_acknowledged=True,
            ),
        )
    app_config = config.app
    if args.host is not None or args.port is not None:
        app_config = replace(
            app_config,
            host=args.host or app_config.host,
            port=args.port or app_config.port,
        )
        config = replace(config, app=app_config)
    if not is_loopback_host(app_config.host):
        raise RuntimeError(
            "MikoType V0.1 is a same-host Windows service and may bind only "
            "to a loopback host"
        )

    runtime = build_runtime(config)
    runtime.start()
    try:
        print(
            f"MikoType running at http://{app_config.host}:{app_config.port} "
            f"with {len(runtime.artifacts.layout.keys)} adaptive keys"
        )
        uvicorn.run(
            runtime.web_app,
            host=app_config.host,
            port=app_config.port,
            log_level=app_config.log_level.lower(),
        )
    finally:
        runtime.stop()
    return 0


def _setup(args: argparse.Namespace) -> int:
    require_windows_runtime()
    config = load_config(args.config)
    if args.acknowledge_mediapipe_metrics:
        config = replace(
            config,
            hand_tracking=replace(config.hand_tracking, metrics_acknowledged=True),
        )
    app_config = replace(
        config.app,
        host=args.host or config.app.host,
        port=args.port or config.app.port,
    )
    if not is_loopback_host(app_config.host):
        raise RuntimeError(
            "keyboard setup mutates local calibration and may bind only to a "
            "loopback host"
        )
    config = replace(config, app=app_config)
    runtime = build_runtime(config)
    controller = KeyboardSetupController(
        active_artifacts=config.artifacts,
        camera_config=config.camera,
        frames=runtime.frames,
        states=runtime.states,
        workspace=SetupWorkspace(args.workspace.expanduser().resolve()),
    )
    runtime.web_app.include_router(create_setup_router(controller))
    runtime.start()
    print(
        f"Keyboard setup running at http://{app_config.host}:{app_config.port}/setup"
    )
    try:
        uvicorn.run(
            runtime.web_app,
            host=app_config.host,
            port=app_config.port,
            log_level=app_config.log_level.lower(),
        )
    finally:
        runtime.stop()
    return 0


def _build_keyboard(args: argparse.Namespace) -> int:
    destination = args.output.expanduser().resolve()
    result = build_keyboard_bundle(
        source_layout=args.layout.expanduser().resolve(),
        source_anchor=args.anchor.expanduser().resolve(),
        source_contact_map=args.contact_map.expanduser().resolve(),
        destination=KeyboardBundlePaths(
            layout=destination / "layout.json",
            anchor=destination / "anchor_reference.json",
            contact_map=destination / "contact_map.json",
            model=destination / "adaptive_keyboard.glb",
            manifest=destination / "adaptive_keyboard_manifest.json",
        ),
    )
    print(
        json.dumps(
            {
                "status": "built",
                "output": str(destination),
                "key_count": result.key_count,
                "model_revision": result.model_revision,
                "model_sha256": result.model_sha256,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return _check(args.config)
        if args.command == "run":
            return _run(args)
        if args.command == "setup":
            return _setup(args)
        if args.command == "build-keyboard":
            return _build_keyboard(args)
        parser.error(f"unknown command: {args.command}")
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"deskvision: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
