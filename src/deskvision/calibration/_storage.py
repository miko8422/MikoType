"""Shared integrity and durable JSON helpers for keyboard artifacts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, TypeVar


ErrorT = TypeVar("ErrorT", bound=Exception)


def canonical_hash(payload: object) -> str:
    """Return the stable SHA-256 used by all keyboard artifact revisions."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_json_object(
    path: Path,
    *,
    artifact_name: str,
    error_factory: Callable[[str], ErrorT],
) -> Mapping[str, Any]:
    """Read one JSON object and translate IO/parse failures to a domain error."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise error_factory(f"cannot load {artifact_name}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise error_factory(f"{artifact_name} root must be an object")
    return payload


def atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    """Atomically replace a JSON artifact after flushing its temporary file.

    The temporary file lives beside the destination, so ``os.replace`` cannot
    cross a filesystem boundary.  A failed write leaves the previous artifact
    intact and removes the temporary file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Atomically replace one binary artifact after an fsync."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


__all__ = [
    "atomic_write_bytes",
    "atomic_write_json",
    "canonical_hash",
    "read_json_object",
]
