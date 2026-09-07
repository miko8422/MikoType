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
from deskvision.web.binding import (
    ServicePortInUseError,
    configuration_revision,
    loopback_url,
    probe_mikotype_service,
    reserve_loopback_endpoint,
    service_identity_mismatch,
)
from deskvision.web.setup import create_setup_router
from deskvision.web.settings import RuntimeSettingsController, create_settings_router


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
        "run", help="start camera, mapping pipeline, and unified local console"
    )
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    run.add_argument("--host")
    run.add_argument("--port", type=int)
    run.add_argument(
        "--auto-port",
        action="store_true",
        help="explicitly allow a bounded search when the requested port is occupied",
    )
    run.add_argument(
        "--workspace",
        type=Path,
        default=Path("data/keyboards/.setup"),
        help="staging directory used by the integrated keyboard setup page",
    )
    run.add_argument(
        "--acknowledge-mediapipe-metrics",
        action="store_true",
        help=(
            "explicitly acknowledge MediaPipe Tasks performance/utilization "
            "metrics for this run"
        ),
    )

    setup = subparsers.add_parser(
        "setup", help="open or start the integrated keyboard calibration UI"
    )
    setup.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    setup.add_argument("--host")
    setup.add_argument("--port", type=int)
    setup.add_argument(
        "--auto-port",
        action="store_true",
        help="explicitly allow a bounded search when the requested port is occupied",
    )
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


def _serve(args: argparse.Namespace, *, landing_path: str) -> int:
    require_windows_runtime()
    configured_config = load_config(args.config)
    config = configured_config
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
            host=args.host if args.host is not None else app_config.host,
            port=args.port if args.port is not None else app_config.port,
        )
    if not is_loopback_host(app_config.host):
        raise RuntimeError(
            "MikoType V0.1 is a same-host Windows service and may bind only "
            "to a loopback host"
        )

    requested_config = replace(config, app=app_config)
    config_path = args.config.expanduser().resolve()
    workspace_path = args.workspace.expanduser().resolve()
    config_revision = configuration_revision(requested_config)

    def mismatch(existing_service) -> str | None:
        return service_identity_mismatch(
            existing_service,
            config_path=config_path,
            workspace_path=workspace_path,
            config_revision=config_revision,
        )

    existing = probe_mikotype_service(app_config.host, app_config.port)
    if existing is not None and mismatch(existing) is None:
        url = loopback_url(app_config.host, app_config.port)
        target = f"{url}{landing_path}" if landing_path != "/" else f"{url}/"
        print(
            "MikoType is already running on the requested port; reuse the "
            f"existing control plane at {target}"
        )
        return 0
    if existing is not None and not args.auto_port:
        raise ServicePortInUseError(
            f"port {app_config.port} is occupied by an incompatible MikoType "
            f"instance: {mismatch(existing)}; use its matching console, stop it, "
            "choose --port <PORT>, or opt in to --auto-port"
        )

    with reserve_loopback_endpoint(
        app_config.host,
        app_config.port,
        allow_fallback=args.auto_port,
    ) as endpoint:
        if endpoint.auto_selected:
            # If another MikoType instance occupies one of the skipped ports,
            # reuse it instead of opening the camera a second time.
            for occupied_port in range(endpoint.configured_port, endpoint.port):
                existing = probe_mikotype_service(app_config.host, occupied_port)
                if existing is None or mismatch(existing) is not None:
                    continue
                url = loopback_url(app_config.host, occupied_port)
                target = (
                    f"{url}{landing_path}" if landing_path != "/" else f"{url}/"
                )
                print(
                    "MikoType is already running in the fallback range; reuse "
                    f"the existing control plane at {target}"
                )
                return 0
            print(
                f"Configured port {endpoint.configured_port} is occupied; "
                f"--auto-port selected {endpoint.port}."
            )
        runtime_config = replace(
            requested_config,
            app=replace(app_config, port=endpoint.port),
        )
        runtime = build_runtime(runtime_config)
        try:
            setup_controller = KeyboardSetupController(
                active_artifacts=runtime_config.artifacts,
                camera_config=runtime_config.camera,
                frames=runtime.frames,
                states=runtime.states,
                workspace=SetupWorkspace(workspace_path),
            )
            settings_controller = RuntimeSettingsController(
                base_config_path=config_path,
                active_config=requested_config,
                actual_host=endpoint.host,
                actual_port=endpoint.port,
                configured_port=endpoint.configured_port,
                auto_selected=endpoint.auto_selected,
                setup_workspace_path=workspace_path,
                mode="setup" if landing_path == "/setup" else "run",
                config_revision=config_revision,
            )
            runtime.web_app.include_router(
                create_settings_router(settings_controller)
            )
            runtime.web_app.include_router(create_setup_router(setup_controller))
            runtime.start()
            url = endpoint.url
            destination = f"{url}{landing_path}" if landing_path != "/" else f"{url}/"
            print(
                f"MikoType control plane running at {destination} with "
                f"{len(runtime.artifacts.layout.keys)} adaptive keys"
            )
            server_config = uvicorn.Config(
                runtime.web_app,
                host=endpoint.host,
                port=endpoint.port,
                log_level=app_config.log_level.lower(),
            )
            uvicorn.Server(server_config).run(sockets=[endpoint.listener])
        finally:
            runtime.stop()
    return 0


def _run(args: argparse.Namespace) -> int:
    return _serve(args, landing_path="/")


def _setup(args: argparse.Namespace) -> int:
    return _serve(args, landing_path="/setup")


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
