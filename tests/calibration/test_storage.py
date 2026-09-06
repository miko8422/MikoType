"""Durability behavior for production artifact writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskvision.calibration._storage import atomic_write_json


pytestmark = pytest.mark.unit


def test_atomic_write_replaces_complete_json_and_leaves_no_temp(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text('{"before": true}\n', encoding="utf-8")

    atomic_write_json(path, {"after": [1, 2, 3]})

    assert json.loads(path.read_text(encoding="utf-8")) == {"after": [1, 2, 3]}
    assert list(tmp_path.glob(".artifact.json.*.tmp")) == []


def test_failed_replace_preserves_old_file_and_cleans_temp(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    original = '{"before": true}\n'
    path.write_text(original, encoding="utf-8")

    def fail_replace(*_args, **_kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr("deskvision.calibration._storage.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_json(path, {"after": True})

    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".artifact.json.*.tmp")) == []
