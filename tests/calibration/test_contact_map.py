"""Production contact-map construction, ranking, and durability checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskvision.calibration import (
    ContactMapError,
    build_contact_map,
    build_layout_inventory,
    load_contact_map,
    nearest_sample_candidates,
    save_contact_map,
)


pytestmark = pytest.mark.unit


def _profile() -> dict[str, object]:
    return {
        "layout_id": "user-board",
        "calibration_order": ["space", "key_a"],
        "keys": [
            {
                "key_id": "key_a",
                "label": "A",
                "browser_code": "KeyA",
                "model_key_id": "key_a",
            },
            {
                "key_id": "space",
                "label": "Space",
                "browser_code": "Space",
                "model_key_id": "space",
            },
        ],
    }


def _samples() -> dict[str, list[tuple[float, float]]]:
    return {
        "space": [(5, 5), (7, 5), (9, 5), (11, 5), (13, 5)],
        "key_a": [(1.2, 4.9), (1.0, 5.0), (0.9, 5.1), (1.1, 5.0), (1.0, 5.2)],
    }


def test_contact_map_round_trip_and_wide_key_sample_ranking(tmp_path: Path) -> None:
    inventory = build_layout_inventory(_profile())
    contact_map = build_contact_map(
        inventory,
        "anchor-r1",
        _samples(),
        created_at="now",
    )
    path = tmp_path / "contact.json"
    save_contact_map(path, contact_map)

    loaded = load_contact_map(
        path,
        inventory=inventory,
        anchor_revision="anchor-r1",
    )
    candidates = nearest_sample_candidates(loaded, (12.8, 5.0), limit=2)

    assert loaded == contact_map
    assert loaded.key("space").center == pytest.approx((9.0, 5.0))
    assert candidates[0].key_id == "space"
    assert candidates[0].nearest_sample_index == 4
    assert candidates[0].distance == pytest.approx(0.2)
    assert candidates[0].weight > candidates[1].weight


def test_contact_map_rejects_tampered_sample_or_revision(tmp_path: Path) -> None:
    inventory = build_layout_inventory(_profile())
    path = tmp_path / "contact.json"
    save_contact_map(path, build_contact_map(inventory, "anchor-r1", _samples()))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["keys"]["space"]["samples"][0][0] = 500
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ContactMapError, match="center|revision"):
        load_contact_map(path)


def test_candidate_arguments_are_validated() -> None:
    inventory = build_layout_inventory(_profile())
    contact_map = build_contact_map(inventory, "anchor-r1", _samples())

    with pytest.raises(ContactMapError, match="positive integer"):
        nearest_sample_candidates(contact_map, (0, 0), limit=0)
    with pytest.raises(ContactMapError, match="positive finite"):
        nearest_sample_candidates(contact_map, (0, 0), distance_scale=0)
    with pytest.raises(ContactMapError, match="non-negative"):
        nearest_sample_candidates(contact_map, (0, 0), max_distance=-1)
