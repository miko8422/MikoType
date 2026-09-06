"""Production keyboard artifact compatibility and integrity checks."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from deskvision.calibration import (
    LAYOUT_PROFILE_SCHEMA_VERSION,
    AnchorReferenceError,
    ContactMapError,
    LayoutProfileError,
    RevisionMismatchError,
    build_layout_inventory,
    load_anchor_reference,
    load_contact_map,
    load_keyboard_calibration_artifacts,
    load_layout_profile,
    save_layout_profile,
)


pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parents[2]
PRODUCTION_BUNDLE = PROJECT_ROOT / "data" / "keyboards" / "kzzi_user_adjustable_82"
LAYOUT_PATH = PRODUCTION_BUNDLE / "layout.json"
ANCHOR_PATH = PRODUCTION_BUNDLE / "anchor_reference.json"
CONTACT_PATH = PRODUCTION_BUNDLE / "contact_map.json"


def test_current_production_artifacts_load_as_one_revision_gated_bundle() -> None:
    artifacts = load_keyboard_calibration_artifacts(
        layout_path=LAYOUT_PATH,
        anchor_path=ANCHOR_PATH,
        contact_map_path=CONTACT_PATH,
    )

    assert artifacts.layout.key_count == 82
    assert artifacts.inventory.revision == (
        "85086cef948159c4efcc0b1433ab6aa4e85ff323fcdc64c1316f947b063a2738"
    )
    assert artifacts.anchor_reference.revision == (
        "8d55ba6424f52b3c621c454022548ebcd92224d21c0921a590d7084ecac3f549"
    )
    assert artifacts.contact_map.revision == (
        "421de70920c7b6ea90bf2c252193ea2437e1ffc9b469dad198bbcd94c0311d4d"
    )
    assert artifacts.contact_map.key_ids == artifacts.inventory.key_ids
    assert len(artifacts.contact_map.key("escape").samples) == 5


def test_legacy_profile_normalizes_to_production_ownership(tmp_path: Path) -> None:
    profile = load_layout_profile(LAYOUT_PATH)
    destination = tmp_path / "layout.json"

    saved = save_layout_profile(destination, profile, timestamp="2026-01-01T00:00:00Z")
    raw = json.loads(destination.read_text(encoding="utf-8"))

    assert raw["schema_version"] == LAYOUT_PROFILE_SCHEMA_VERSION
    assert raw["key_count"] == 82
    assert raw["created_at"] == profile.created_at
    assert raw["updated_at"] == "2026-01-01T00:00:00Z"
    assert load_layout_profile(destination) == saved


def test_layout_geometry_does_not_invalidate_physical_inventory() -> None:
    raw = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    before = build_layout_inventory(raw)
    moved = deepcopy(raw)
    moved["keys"][10]["x_units"] += 0.5
    moved["keys"][10]["width_units"] += 0.2
    after = build_layout_inventory(moved)

    assert before.revision == after.revision

    moved["calibration_order"][0], moved["calibration_order"][1] = (
        moved["calibration_order"][1],
        moved["calibration_order"][0],
    )
    assert build_layout_inventory(moved).revision != before.revision


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload["keys"].append(payload["keys"][0]), "duplicate"),
        (lambda payload: payload["anchors"].append(payload["anchors"][0]), "duplicate"),
        (lambda payload: payload["keys"][0].update({"width_units": 0}), "positive"),
    ],
)
def test_layout_rejects_invalid_identity_and_geometry(mutation, message: str) -> None:
    payload = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    mutation(payload)
    with pytest.raises(LayoutProfileError, match=message):
        from deskvision.calibration.layout_profile import layout_profile_from_dict

        layout_profile_from_dict(payload)


def test_bundle_fails_closed_for_stale_contact_or_wrong_anchor_plan(tmp_path: Path) -> None:
    contact = json.loads(CONTACT_PATH.read_text(encoding="utf-8"))
    contact["layout_revision"] = "stale"
    stale_path = tmp_path / "stale-contact.json"
    stale_path.write_text(json.dumps(contact), encoding="utf-8")
    with pytest.raises(ContactMapError, match="revision"):
        load_keyboard_calibration_artifacts(
            layout_path=LAYOUT_PATH,
            anchor_path=ANCHOR_PATH,
            contact_map_path=stale_path,
        )

    layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    layout["anchors"][0]["key_id"] = "f1"
    changed_layout_path = tmp_path / "wrong-plan.json"
    changed_layout_path.write_text(json.dumps(layout), encoding="utf-8")
    with pytest.raises(AnchorReferenceError, match="layout anchor plan"):
        load_keyboard_calibration_artifacts(
            layout_path=changed_layout_path,
            anchor_path=ANCHOR_PATH,
            contact_map_path=CONTACT_PATH,
        )


def test_individual_loaders_can_enforce_revision_relationships() -> None:
    profile = load_layout_profile(LAYOUT_PATH)
    inventory = build_layout_inventory(profile)
    anchor = load_anchor_reference(ANCHOR_PATH, inventory=inventory)

    with pytest.raises(RevisionMismatchError, match="anchor"):
        load_contact_map(
            CONTACT_PATH,
            inventory=inventory,
            anchor_revision="another-anchor",
        )
    loaded = load_contact_map(
        CONTACT_PATH,
        inventory=inventory,
        anchor_revision=anchor.revision,
    )
    assert loaded.layout_revision == profile.inventory_revision
