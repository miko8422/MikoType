"""Build a deterministic, source-only Windows SteamVR handoff ZIP."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Iterable
import zipfile


BUNDLE_SCHEMA_VERSION = "steamvr-home-windows-source-bundle-0.1"
BUNDLE_ROOT_NAME = "deskvision_steamvr_home_smoke_source"
RENDER_MODEL_NAME = "deskvision_keyboard"
HIGHLIGHT_FILENAME = "keyboard_highlight_test.png"
MAX_BUNDLE_MEMBER_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


class SourceBundleError(ValueError):
    """A Windows source bundle cannot be assembled safely."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _has_unsafe_windows_part(path: PurePosixPath) -> bool:
    for part in path.parts:
        if (
            not part
            or part in {".", ".."}
            or part != part.rstrip(" .")
            or any(ord(character) < 32 for character in part)
            or any(character in '<>:"\\|?*' for character in part)
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
        ):
            return True
    return False


def _safe_archive_path(path: PurePosixPath) -> str:
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ) or _has_unsafe_windows_part(path):
        raise SourceBundleError(f"unsafe ZIP member path: {path}")
    return path.as_posix()


def _source_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        raise SourceBundleError(f"required source directory is missing: {root.name}")
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise SourceBundleError(f"source bundle does not follow symlinks: {path}")
        if path.is_file() and not any(
            part in {"build", ".build", "dist", "__pycache__"}
            for part in path.relative_to(root).parts
        ):
            yield path


def _render_files(export_directory: Path) -> tuple[Path, ...]:
    if not export_directory.is_dir():
        raise SourceBundleError("OpenVR export directory does not exist")
    files = tuple(
        path
        for path in sorted(export_directory.iterdir(), key=lambda item: item.name)
        if path.is_file() and not path.is_symlink()
    )
    names = {path.name for path in files}
    manifest_path = export_directory / "export_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceBundleError(f"OpenVR export manifest is invalid: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("outputs"), dict):
        raise SourceBundleError("OpenVR export manifest contains no output records")
    output_records = manifest["outputs"]
    declared_names = set(output_records)
    if names != declared_names | {"export_manifest.json"}:
        raise SourceBundleError("OpenVR export directory differs from its manifest")
    for filename, raw_record in output_records.items():
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not isinstance(raw_record, dict)
        ):
            raise SourceBundleError("OpenVR export manifest has an unsafe output record")
        path = export_directory / filename
        payload = path.read_bytes()
        if (
            raw_record.get("byte_length") != len(payload)
            or raw_record.get("sha256") != _sha256(payload)
        ):
            raise SourceBundleError(f"OpenVR export hash mismatch: {filename}")
    required = {
        "export_manifest.json",
        f"{RENDER_MODEL_NAME}.json",
        HIGHLIGHT_FILENAME,
    }
    missing = sorted(required - names)
    if missing:
        raise SourceBundleError(
            "OpenVR export is incomplete: missing " + ", ".join(missing)
        )
    if not any(path.suffix.casefold() == ".obj" for path in files):
        raise SourceBundleError("OpenVR export contains no OBJ geometry")
    if not any(path.suffix.casefold() == ".mtl" for path in files):
        raise SourceBundleError("OpenVR export contains no MTL material")
    return files


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    return info


def _entries_fingerprint(entries: dict[str, bytes]) -> str:
    """Hash the exact logical Windows handoff inputs, independent of mtimes."""

    digest = hashlib.sha256()
    for name, payload in sorted(entries.items()):
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _bundle_input_entries(module_root: Path, export_root: Path) -> dict[str, bytes]:
    readme_path = module_root / "README.md"
    if not readme_path.is_file():
        raise SourceBundleError("Demo README.md is missing")
    source_roots = {
        "windows_driver": module_root / "windows_driver",
        "windows_overlay": module_root / "windows_overlay",
        "packaging": module_root / "packaging",
    }
    render_files = _render_files(export_root)
    entries: dict[str, bytes] = {}

    def add(relative: PurePosixPath, payload: bytes) -> None:
        member = _safe_archive_path(PurePosixPath(BUNDLE_ROOT_NAME) / relative)
        if member in entries:
            raise SourceBundleError(f"duplicate ZIP member: {member}")
        entries[member] = payload

    add(PurePosixPath("README.md"), readme_path.read_bytes())
    for label, root in source_roots.items():
        for path in _source_files(root):
            relative = PurePosixPath(label) / PurePosixPath(
                path.relative_to(root).as_posix()
            )
            add(relative, path.read_bytes())

    render_target = (
        PurePosixPath("windows_driver")
        / "deskvisionkeyboard"
        / "resources"
        / "rendermodels"
        / RENDER_MODEL_NAME
    )
    for path in render_files:
        if path.name == "export_manifest.json":
            add(PurePosixPath("asset_export") / path.name, path.read_bytes())
        else:
            add(render_target / path.name, path.read_bytes())

    add(
        PurePosixPath("windows_overlay") / "assets" / HIGHLIGHT_FILENAME,
        (export_root / HIGHLIGHT_FILENAME).read_bytes(),
    )
    return entries


def source_bundle_input_fingerprint(
    *, module_directory: str | Path, export_directory: str | Path
) -> str:
    """Return the content identity that makes a generated bundle current."""

    return _entries_fingerprint(
        _bundle_input_entries(
            Path(module_directory).resolve(), Path(export_directory).resolve()
        )
    )


def validate_windows_source_bundle(path: str | Path) -> dict[str, object]:
    """Recompute every internal hash before a generated ZIP is advertised."""

    bundle_path = Path(path)
    try:
        bundle_payload = bundle_path.read_bytes()
        with zipfile.ZipFile(bundle_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if names != sorted(names) or len(names) != len(set(names)):
                raise SourceBundleError("Windows source ZIP members are not sorted and unique")
            total_size = 0
            for info in infos:
                member = PurePosixPath(info.filename)
                if (
                    member.is_absolute()
                    or not member.parts
                    or member.parts[0] != BUNDLE_ROOT_NAME
                    or any(part in {"", ".", ".."} for part in member.parts)
                    or _has_unsafe_windows_part(member)
                    or info.is_dir()
                ):
                    raise SourceBundleError(f"unsafe Windows source ZIP member: {info.filename}")
                if info.file_size > MAX_BUNDLE_MEMBER_BYTES:
                    raise SourceBundleError(f"oversized Windows source ZIP member: {info.filename}")
                total_size += info.file_size
            if total_size > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                raise SourceBundleError("Windows source ZIP exceeds its uncompressed size limit")

            prefix = f"{BUNDLE_ROOT_NAME}/"
            checksum_name = f"{prefix}SHA256SUMS.json"
            status_name = f"{prefix}SOURCE_BUNDLE_STATUS.json"
            if checksum_name not in names or status_name not in names:
                raise SourceBundleError("Windows source ZIP is missing its status or checksums")
            checksums = json.loads(archive.read(checksum_name).decode("utf-8"))
            status = json.loads(archive.read(status_name).decode("utf-8"))
            if (
                not isinstance(checksums, dict)
                or checksums.get("schema_version") != "source-bundle-sha256sums-0.1"
                or not isinstance(checksums.get("files"), dict)
            ):
                raise SourceBundleError("Windows source ZIP checksum manifest is invalid")
            if (
                not isinstance(status, dict)
                or status.get("schema_version") != BUNDLE_SCHEMA_VERSION
                or status.get("claim")
                != "windows-source-only-not-built-not-steamvr-verified"
                or not isinstance(status.get("input_fingerprint"), str)
                or len(status["input_fingerprint"]) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in status["input_fingerprint"]
                )
            ):
                raise SourceBundleError("Windows source ZIP status claim is invalid")
            records = checksums["files"]
            expected_names = set(names) - {checksum_name}
            if set(records) != expected_names:
                raise SourceBundleError("Windows source ZIP checksum coverage is incomplete")
            for name, raw_record in records.items():
                if not isinstance(raw_record, dict):
                    raise SourceBundleError(f"invalid checksum record: {name}")
                payload = archive.read(name)
                if (
                    raw_record.get("byte_length") != len(payload)
                    or raw_record.get("sha256") != _sha256(payload)
                ):
                    raise SourceBundleError(f"Windows source ZIP hash mismatch: {name}")
    except SourceBundleError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, zipfile.BadZipFile) as exc:
        raise SourceBundleError(f"Windows source ZIP is invalid: {exc}") from exc

    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "filename": bundle_path.name,
        "sha256": _sha256(bundle_payload),
        "byte_length": len(bundle_payload),
        "file_count": len(names),
        "claim": status["claim"],
        "input_fingerprint": status["input_fingerprint"],
    }


def build_windows_source_bundle(
    *,
    module_directory: str | Path,
    export_directory: str | Path,
    output_path: str | Path,
) -> dict[str, object]:
    """Package sources and generated assets without claiming a Windows build.

    The generated render-model files are injected into the Driver resource
    tree and the static highlight texture is also placed beside the Overlay
    source asset path expected by its CMake install rule.
    """

    module_root = Path(module_directory).resolve()
    export_root = Path(export_directory).resolve()
    destination = Path(output_path).resolve()
    if module_root == export_root or destination == module_root:
        raise SourceBundleError("source, export, and bundle paths must differ")
    protected_roots = (
        export_root,
        module_root / "windows_driver",
        module_root / "windows_overlay",
        module_root / "packaging",
    )
    if destination == module_root / "README.md" or any(
        destination == root or destination.is_relative_to(root)
        for root in protected_roots
    ):
        raise SourceBundleError("bundle output cannot overwrite source or export inputs")

    entries = _bundle_input_entries(module_root, export_root)
    input_fingerprint = _entries_fingerprint(entries)

    status = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "claim": "windows-source-only-not-built-not-steamvr-verified",
        "driver_name": "deskvisionkeyboard",
        "render_model_name": RENDER_MODEL_NAME,
        "fixed_pose_only": True,
        "dynamic_scene_state_bridge_included": False,
        "input_fingerprint": input_fingerprint,
        "gates": {
            "asset_bundle_validated": True,
            "windows_source_bundle_validated": True,
            "windows_driver_built": False,
            "steamvr_driver_loaded": False,
            "home_model_visible": False,
            "overlay_aligned": False,
        },
    }
    status_name = _safe_archive_path(
        PurePosixPath(BUNDLE_ROOT_NAME) / "SOURCE_BUNDLE_STATUS.json"
    )
    if status_name in entries:
        raise SourceBundleError(f"duplicate ZIP member: {status_name}")
    entries[status_name] = _json_bytes(status)

    checksums = {
        name: {"sha256": _sha256(payload), "byte_length": len(payload)}
        for name, payload in sorted(entries.items())
    }
    checksum_name = _safe_archive_path(
        PurePosixPath(BUNDLE_ROOT_NAME) / "SHA256SUMS.json"
    )
    entries[checksum_name] = _json_bytes(
        {
            "schema_version": "source-bundle-sha256sums-0.1",
            "files": checksums,
        }
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for name, payload in sorted(entries.items()):
                archive.writestr(_zip_info(name), payload)
        os.replace(temporary, destination)
        temporary = None
    except (OSError, zipfile.BadZipFile) as exc:
        raise SourceBundleError(f"cannot publish Windows source bundle: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    return validate_windows_source_bundle(destination)


__all__ = [
    "BUNDLE_ROOT_NAME",
    "BUNDLE_SCHEMA_VERSION",
    "HIGHLIGHT_FILENAME",
    "RENDER_MODEL_NAME",
    "SourceBundleError",
    "build_windows_source_bundle",
    "source_bundle_input_fingerprint",
    "validate_windows_source_bundle",
]
