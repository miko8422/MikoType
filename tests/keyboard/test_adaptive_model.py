"""Production contract tests for the adaptive physical-key GLB generator."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import struct

import pytest

from deskvision.calibration import (
    KeyboardKey,
    KeyboardLayoutProfile,
    build_contact_map,
    build_layout_inventory,
)
from deskvision.keyboard import adaptive_model as model_module
from deskvision.keyboard.adaptive_model import (
    AdaptiveKeyboardModelError,
    MODEL_SCHEMA_VERSION,
    NODE_CONTRACT,
    generate_adaptive_keyboard_model,
    layout_geometry_revision,
)


pytestmark = pytest.mark.unit


def _profile(*, count: int = 82) -> KeyboardLayoutProfile:
    keys = tuple(
        KeyboardKey(
            key_id=f"key_{index:02d}",
            label=f"K{index:02d}",
            browser_code=f"Key{index:02d}",
            physical_key_id=f"key_{index:02d}",
            model_key_id=(f"legacy_surface_{index}" if index < 3 else f"key_{index:02d}"),
            row=index // 14,
            x_units=(index % 14) * 1.1,
            y_units=(index // 14) * 1.2,
            width_units=1.0,
            height_units=1.0,
            is_wide=False,
        )
        for index in range(count)
    )
    return KeyboardLayoutProfile(
        layout_id="fixture-82-physical-keys",
        layout_name="Fixture physical keyboard",
        source_layout="test",
        unit="keyboard_1u",
        row_step_units=1.2,
        keys=keys,
        anchors=(),
        calibration_order=tuple(key.key_id for key in keys),
    )


def _contact_map(profile: KeyboardLayoutProfile, *, shift: float = 0.0):
    inventory = build_layout_inventory(profile)
    samples = {}
    for index, key_id in enumerate(inventory.key_ids):
        center_x = float(index % 14) + shift
        center_y = float(index // 14) + shift / 2
        samples[key_id] = (
            (center_x - 0.04, center_y),
            (center_x - 0.02, center_y + 0.01),
            (center_x, center_y),
            (center_x + 0.02, center_y - 0.01),
            (center_x + 0.04, center_y),
        )
    return build_contact_map(
        inventory,
        "fixture-anchor-revision",
        samples,
        created_at="2026-09-05T00:00:00Z",
    )


def _parse_glb(payload: bytes) -> tuple[dict[str, object], bytes]:
    magic, version, declared_length = struct.unpack_from("<4sII", payload, 0)
    assert magic == b"glTF"
    assert version == 2
    assert declared_length == len(payload)
    json_length, json_type = struct.unpack_from("<II", payload, 12)
    assert json_type == 0x4E4F534A
    json_start = 20
    json_end = json_start + json_length
    binary_length, binary_type = struct.unpack_from("<II", payload, json_end)
    assert binary_type == 0x004E4942
    binary_start = json_end + 8
    assert binary_start + binary_length == len(payload)
    document = json.loads(payload[json_start:json_end].rstrip(b" \t\r\n\0"))
    return document, payload[binary_start:]


def test_generation_is_deterministic_and_bound_to_82_physical_key_ids() -> None:
    profile = _profile()
    contact_map = _contact_map(profile)

    first = generate_adaptive_keyboard_model(profile, contact_map)
    second = generate_adaptive_keyboard_model(profile, contact_map)

    assert first == second
    assert first.manifest["schema_version"] == MODEL_SCHEMA_VERSION
    assert first.manifest["node_contract"] == NODE_CONTRACT
    assert first.manifest["key_count"] == 82
    assert first.manifest["contact_binding"]["type"] == "physical_key_id"
    assert [key["key_id"] for key in first.manifest["keys"]] == [
        key.key_id for key in profile.keys
    ]
    assert first.manifest["model"]["sha256"] == hashlib.sha256(first.glb).hexdigest()
    assert first.manifest["model"]["byte_length"] == len(first.glb)
    assert first.manifest["model"]["self_contained"] is True
    assert first.manifest["model"]["external_asset_used"] is False
    assert "legacy_model_key_id" not in json.dumps(first.manifest)
    assert "legacy_surface" not in json.dumps(first.manifest)


def test_glb_is_self_contained_with_one_independent_node_per_physical_key() -> None:
    profile = _profile()
    generated = generate_adaptive_keyboard_model(profile, _contact_map(profile))
    document, binary = _parse_glb(generated.glb)

    assert document["asset"]["version"] == "2.0"
    assert document["asset"]["extras"]["model_revision"] == generated.manifest[
        "model_revision"
    ]
    assert "extensionsRequired" not in document
    assert len(document["buffers"]) == 1
    assert "uri" not in document["buffers"][0]
    assert document["buffers"][0]["byteLength"] <= len(binary)

    key_nodes = [
        node
        for node in document["nodes"]
        if node.get("extras", {}).get("role") == "keyboard_key"
    ]
    assert len(key_nodes) == 82
    assert len({node["extras"]["key_id"] for node in key_nodes}) == 82
    by_id = {node["extras"]["key_id"]: node for node in key_nodes}
    assert set(by_id) == {key.key_id for key in profile.keys}
    for key_id, node in by_id.items():
        assert node["name"] == f"key:{key_id}"
        assert node["extras"]["model_revision"] == generated.manifest["model_revision"]
        assert "legacy_model_key_id" not in node["extras"]

    root = next(
        node
        for node in document["nodes"]
        if node.get("extras", {}).get("role") == "adaptive_keyboard_root"
    )
    assert root["extras"]["node_contract"] == NODE_CONTRACT
    assert len(root["children"]) == 83  # case + 82 independent key nodes

    for view in document["bufferViews"]:
        assert view["buffer"] == 0
        assert view.get("byteOffset", 0) % 4 == 0
        assert view["byteLength"] > 0
        assert view.get("byteOffset", 0) + view["byteLength"] <= document[
            "buffers"
        ][0]["byteLength"]


def test_geometry_contact_and_recipe_revisions_change_only_for_their_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile()
    contact_map = _contact_map(profile)
    original = generate_adaptive_keyboard_model(profile, contact_map)

    moved_keys = list(profile.keys)
    moved_keys[0] = replace(moved_keys[0], x_units=moved_keys[0].x_units + 0.05)
    moved_profile = replace(profile, keys=tuple(moved_keys))
    moved = generate_adaptive_keyboard_model(moved_profile, contact_map)
    assert moved.manifest["geometry_revision"] != original.manifest["geometry_revision"]
    assert moved.manifest["model_revision"] != original.manifest["model_revision"]
    assert moved.manifest["model"]["sha256"] != original.manifest["model"]["sha256"]
    assert moved.manifest["contact_binding"]["contact_map_revision"] == contact_map.revision

    recalibrated = generate_adaptive_keyboard_model(profile, _contact_map(profile, shift=0.2))
    assert recalibrated.manifest["geometry_revision"] == original.manifest[
        "geometry_revision"
    ]
    assert recalibrated.manifest["model_revision"] != original.manifest["model_revision"]
    assert recalibrated.manifest["model"]["sha256"] != original.manifest["model"][
        "sha256"
    ]
    assert [key["center"] for key in recalibrated.manifest["keys"]] == [
        key["center"] for key in original.manifest["keys"]
    ]

    monkeypatch.setattr(
        model_module,
        "KEY_CLEARANCE_M",
        model_module.KEY_CLEARANCE_M + 0.001,
    )
    recipe_changed = generate_adaptive_keyboard_model(profile, contact_map)
    assert recipe_changed.manifest["geometry_revision"] == original.manifest[
        "geometry_revision"
    ]
    assert recipe_changed.manifest["model_revision"] != original.manifest[
        "model_revision"
    ]
    assert recipe_changed.manifest["model"]["sha256"] != original.manifest["model"][
        "sha256"
    ]


def test_layout_geometry_revision_ignores_legacy_surface_alias() -> None:
    profile = _profile()
    keys = list(profile.keys)
    keys[0] = replace(keys[0], model_key_id="an-entirely-different-old-surface")
    changed_alias = replace(profile, keys=tuple(keys))

    assert layout_geometry_revision(changed_alias) == layout_geometry_revision(profile)


def test_overlapping_layout_rectangles_fail_closed() -> None:
    profile = _profile()
    keys = list(profile.keys)
    keys[1] = replace(
        keys[1],
        x_units=keys[0].x_units,
        y_units=keys[0].y_units,
    )
    overlapping = replace(profile, keys=tuple(keys))

    with pytest.raises(AdaptiveKeyboardModelError, match="overlap"):
        generate_adaptive_keyboard_model(overlapping, _contact_map(overlapping))


def test_nonidentical_physical_key_alias_fails_closed() -> None:
    profile = _profile()
    keys = list(profile.keys)
    keys[0] = replace(keys[0], physical_key_id="different-physical-key")
    aliased = replace(profile, keys=tuple(keys))

    with pytest.raises(AdaptiveKeyboardModelError, match="physical_key_id"):
        generate_adaptive_keyboard_model(aliased, _contact_map(aliased))
