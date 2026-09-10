"""Command-line entry point for production runtime and keyboard artifacts."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

import uvicorn

from deskvision import __version__
from deskvision.cli_diagnostics import (
    doctor as _doctor,
    print_version as _print_version,
    runtime_identity as _runtime_identity,
)
from deskvision.calibration.artifacts import load_keyboard_calibration_artifacts
from deskvision.calibration.control_plane import KeyboardSetupController, SetupWorkspace
from deskvision.core.config import load_config
from deskvision.core.platform import is_loopback_host, require_windows_runtime, require_core_runtime
from deskvision.core.local_workspace import initialize_mac_workspace
from deskvision.keyboard.bundle import KeyboardBundlePaths, build_keyboard_bundle
from deskvision.runtime import (
    build_runtime,
    ensure_keyboard_model,
    validate_runtime_config,
)
from deskvision.web.binding import (
    AUTO_PORT_START,
    AUTO_PORT_END,
    ServicePortInUseError,
    canonical_loopback_host,
    candidate_ports,
    configuration_revision,
    discover_mikotype_services,
    loopback_url,
    probe_mikotype_service,
    reserve_loopback_endpoint,
    service_identity_mismatch,
)
from deskvision.web.setup import create_setup_router
from deskvision.web.settings import RuntimeSettingsController, create_settings_router
from deskvision.web.camera import CameraController, create_camera_router
from deskvision.runtime_camera import RuntimeCameraSession


DEFAULT_CONFIG = Path("configs/windows.yaml")


def _print_startup_identity(
    *, config_path: Path, host: str, port: int, auto_port: bool
) -> None:
    identity = _runtime_identity()
    print("\n=== MIKOTYPE STARTUP ===", flush=True)
    print(
        f"Version: {identity['package_version']} "
        f"({identity['service_schema']})",
        flush=True,
    )
    print(f"Python: {identity['python_executable']}", flush=True)
    print(f"Source: {identity['runtime_source']}", flush=True)
    print(f"Config: {config_path}", flush=True)
    if auto_port:
        policy = f"automatic ({AUTO_PORT_START}-{AUTO_PORT_END}, preferred {port})"
    else:
        policy = f"strict ({port})"
    print(f"Port policy: {policy} on {host}", flush=True)
    if identity["source_matches_current_checkout"] is False:
        raise RuntimeError(
            "This command is loading MikoType from a different checkout. "
            "Start this checkout with python .\\run_mikotype.py run "
            "--config configs\\windows.yaml, or repair the environment with "
            "python -m pip install --force-reinstall --no-deps -e ."
        )


def _add_port_selection(parser: argparse.ArgumentParser) -> None:
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--auto-port",
        dest="auto_port",
        action="store_true",
        help="select an available port in 9000-10000 inclusive (default)",
    )
    selection.add_argument(
        "--strict-port",
        dest="auto_port",
        action="store_false",
        help="fail instead of moving when the preferred port is occupied",
    )
    parser.set_defaults(auto_port=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mikotype",
        description="Local camera and adaptive keyboard mapping; SteamVR is tested separately on Windows",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="show the loaded MikoType version, Python, and source path",
    )
    subparsers = parser.add_subparsers(dest="command")

    doctor = subparsers.add_parser(
        "doctor", help="diagnose the active Python/install/config without hardware"
    )
    doctor.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

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
    _add_port_selection(run)
    run.add_argument(
        "--workspace",
        type=Path,
        default=None,
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
    _add_port_selection(setup)
    setup.add_argument(
        "--workspace",
        type=Path,
        default=None,
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
    config = load_config(args.config)
    if config.deployment.target_os == "windows":
        require_windows_runtime()
    else:
        require_core_runtime(config.deployment.target_os)
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
            "MikoType V0.1 is a same-host service and may bind only "
            "to a loopback host"
        )
    app_config = replace(
        app_config,
        host=canonical_loopback_host(app_config.host),
    )

    requested_config = replace(config, app=app_config)
    config_path = args.config.expanduser().resolve()
    default_workspace = (
        Path(__file__).resolve().parents[2] / "data/local/macos/.setup"
        if config.deployment.target_os == "macos" else Path("data/keyboards/.setup")
    )
    workspace_path = (args.workspace or default_workspace).expanduser().resolve()
    config_revision = configuration_revision(requested_config)
    _print_startup_identity(
        config_path=config_path,
        host=app_config.host,
        port=app_config.port,
        auto_port=args.auto_port,
    )

    def mismatch(existing_service) -> str | None:
        return service_identity_mismatch(
            existing_service,
            config_path=config_path,
            workspace_path=workspace_path,
            config_revision=config_revision,
        )

    def reuse_existing(port: int, *, discovered: bool = False) -> int:
        url = loopback_url(app_config.host, port)
        target = f"{url}{landing_path}" if landing_path != "/" else f"{url}/"
        location = "discovered fallback port" if discovered else "preferred port"
        print(
            f"MikoType is already running on the {location}.\n"
            f"OPEN THIS EXACT URL: {target}",
            flush=True,
        )
        return 0

    if args.auto_port:
        candidates = candidate_ports(app_config.port, allow_fallback=True)
        services = discover_mikotype_services(app_config.host, candidates)
        for candidate_port, service in services.items():
            if mismatch(service) is None:
                return reuse_existing(
                    candidate_port, discovered=candidate_port != app_config.port
                )
    else:
        existing = probe_mikotype_service(app_config.host, app_config.port)
        if existing is not None:
            reason = mismatch(existing)
            if reason is None:
                return reuse_existing(app_config.port)
            raise ServicePortInUseError(
                f"port {app_config.port} is occupied by an incompatible MikoType "
                f"instance: {reason}; use its matching console, "
                "choose --port <PORT>, or remove --strict-port"
            )

    with reserve_loopback_endpoint(
        app_config.host,
        app_config.port,
        allow_fallback=args.auto_port,
    ) as endpoint:
        if endpoint.auto_selected:
            print(
                f"Configured port {endpoint.configured_port} was not selected; "
                f"reserved available port {endpoint.port} in "
                f"{AUTO_PORT_START}-{AUTO_PORT_END}.",
                flush=True,
            )
        runtime_config = replace(
            requested_config,
            app=replace(app_config, port=endpoint.port),
        )
        initialize_mac_workspace(runtime_config, repository=Path(__file__).resolve().parents[2])
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
                package_version=__version__,
                runtime_source=str(Path(__file__).resolve()),
                python_executable=sys.executable,
            )
            runtime.web_app.include_router(
                create_settings_router(settings_controller)
            )
            runtime.web_app.include_router(create_setup_router(setup_controller))
            camera_session = RuntimeCameraSession(runtime, settings_controller, setup_controller)
            runtime.web_app.include_router(create_camera_router(CameraController(
                status=camera_session.status,
                scan=camera_session.scan,
                apply=camera_session.apply,
            )))
            runtime.start(allow_camera_failure=True)
            url = endpoint.url
            destination = f"{url}{landing_path}" if landing_path != "/" else f"{url}/"

            def report_ready() -> None:
                print("\n=== MIKOTYPE READY ===", flush=True)
                print(
                    f"OPEN THIS EXACT URL: {destination}\n"
                    f"SteamVR status and logs: {url}/steamvr\n"
                    f"Adaptive keyboard: {len(runtime.artifacts.layout.keys)} keys\n",
                    flush=True,
                )

            class ReadyServer(uvicorn.Server):
                async def startup(self, sockets=None) -> None:
                    await super().startup(sockets=sockets)
                    if self.started:
                        report_ready()

            server_config = uvicorn.Config(
                runtime.web_app,
                host=endpoint.host,
                port=endpoint.port,
                log_level=app_config.log_level.lower(),
                workers=1,
                timeout_graceful_shutdown=runtime_config.pipeline.stop_timeout_s,
            )
            server = ReadyServer(server_config)
            try:
                server.run(sockets=[endpoint.listener])
            except SystemExit as exc:
                raise RuntimeError(
                    f"MikoType web service failed to start (exit {exc.code})"
                ) from exc
            if not server.started:
                raise RuntimeError("MikoType web service stopped before it was ready")
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
        if args.version:
            return _print_version()
        if args.command is None:
            parser.error("a command is required")
        if args.command == "doctor":
            return _doctor(args.config)
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
