"""Read-only capability detection for the SteamVR Home hybrid Demo.

The probe deliberately does not import OpenVR, launch Steam, invoke
``vrpathreg``, or mutate driver registration.  It only inspects the current
platform, tools available on ``PATH``, and an optional SteamVR directory.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import platform
import shutil
from typing import Callable, Mapping


_WINDOWS_X64_MACHINES = {"amd64", "x86_64", "x64"}
_WINDOWS_BUILD_TOOLS = ("cl", "clang-cl", "msbuild")


@dataclass(frozen=True, slots=True)
class EnvironmentCapabilities:
    """Capabilities proven by a non-mutating local inspection."""

    platform_system: str
    machine: str
    asset_export: bool
    state_replay: bool
    mock_compositor: bool
    driver_build: bool
    steamvr_runtime: bool
    steamvr_root: str | None
    build_tools: tuple[str, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _default_steamvr_roots(environ: Mapping[str, str]) -> tuple[Path, ...]:
    candidates: list[Path] = []
    configured = environ.get("STEAMVR_ROOT")
    if configured:
        candidates.append(Path(configured).expanduser())

    for variable in ("PROGRAMFILES(X86)", "ProgramFiles(x86)", "PROGRAMFILES"):
        root = environ.get(variable)
        if root:
            candidates.append(
                Path(root) / "Steam" / "steamapps" / "common" / "SteamVR"
            )

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return tuple(unique)


def _has_runtime_files(root: Path) -> bool:
    win64 = root / "bin" / "win64"
    return (win64 / "vrpathreg.exe").is_file() and (
        win64 / "vrserver.exe"
    ).is_file()


def probe_environment(
    *,
    platform_system: str | None = None,
    machine: str | None = None,
    steamvr_root: str | Path | None = None,
    which: Callable[[str], str | None] | None = None,
    environ: Mapping[str, str] | None = None,
) -> EnvironmentCapabilities:
    """Return capabilities that are actually available in this process.

    Parameters are injectable so Windows branches can be tested safely on a
    non-Windows development host. Supplying a pretend platform never executes a tool;
    the function remains a filesystem and ``PATH`` inspection only.
    """

    detected_system = (platform_system or platform.system()).strip()
    detected_machine = (machine or platform.machine()).strip()
    normalized_system = detected_system.casefold()
    normalized_machine = detected_machine.casefold()
    environment = os.environ if environ is None else environ
    find_tool = shutil.which if which is None else which

    is_windows_x64 = (
        normalized_system == "windows"
        and normalized_machine in _WINDOWS_X64_MACHINES
    )

    discovered_tools: list[str] = []
    cmake = find_tool("cmake") if is_windows_x64 else None
    if cmake:
        discovered_tools.append("cmake")
    compiler_or_builder = None
    if is_windows_x64:
        for tool in _WINDOWS_BUILD_TOOLS:
            if find_tool(tool):
                compiler_or_builder = tool
                discovered_tools.append(tool)
                break
    driver_build = bool(is_windows_x64 and cmake and compiler_or_builder)

    if steamvr_root is not None:
        runtime_candidates = (Path(steamvr_root).expanduser(),)
    else:
        runtime_candidates = _default_steamvr_roots(environment)

    detected_runtime_root: Path | None = None
    if is_windows_x64:
        detected_runtime_root = next(
            (candidate for candidate in runtime_candidates if _has_runtime_files(candidate)),
            None,
        )
    steamvr_runtime = detected_runtime_root is not None

    notes = [
        "read-only probe: SteamVR was not launched and no driver was registered"
    ]
    if not is_windows_x64:
        notes.append("OpenVR driver build and runtime checks require Windows x64")
    elif not driver_build:
        notes.append("driver build requires CMake and MSVC/clang-cl/MSBuild on PATH")
    if is_windows_x64 and not steamvr_runtime:
        notes.append("SteamVR runtime files vrpathreg.exe and vrserver.exe were not found")

    return EnvironmentCapabilities(
        platform_system=detected_system,
        machine=detected_machine,
        asset_export=True,
        state_replay=True,
        mock_compositor=True,
        driver_build=driver_build,
        steamvr_runtime=steamvr_runtime,
        steamvr_root=(
            None if detected_runtime_root is None else str(detected_runtime_root)
        ),
        build_tools=tuple(discovered_tools),
        notes=tuple(notes),
    )


__all__ = ["EnvironmentCapabilities", "probe_environment"]
