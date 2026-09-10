"""Production OpenVR assets preserve arbitrary user keyboard inventories."""

from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct

import pytest

from deskvision.calibration import (
    KeyboardKey,
    KeyboardLayoutProfile,
    build_contact_map,
    build_layout_inventory,
)
from deskvision.keyboard.adaptive_model import generate_adaptive_keyboard_model
from deskvision.steamvr import assets


pytestmark = pytest.mark.unit


def _source_pair(tmp_path: Path, count: int = 7):
    keys = tuple(
        KeyboardKey(
            key_id=f"custom_{index}",
            label=f"K{index}",
            browser_code=f"Key{index}",
            physical_key_id=f"custom_{index}",
            model_key_id=f"custom_{index}",
            row=index // 14,
            x_units=float(index % 14),
            y_units=float(index // 14),
            width_units=1.0,
            height_units=1.0,
            is_wide=False,
        )
        for index in range(count)
    )
    profile = KeyboardLayoutProfile(
        layout_id=f"custom-{count}",
        layout_name="User defined test keyboard",
        source_layout="test",
        unit="keyboard_1u",
        row_step_units=1.0,
        keys=keys,
        anchors=(),
        calibration_order=tuple(key.key_id for key in keys),
    )
    contact = build_contact_map(
        build_layout_inventory(profile),
        "test-anchor-revision",
        {
            key.key_id: tuple(
                (key.x_units + offset, key.y_units + offset / 2)
                for offset in (-0.04, -0.02, 0.0, 0.02, 0.04)
            )
            for key in keys
        },
        created_at="2026-09-10T00:00:00Z",
    )
    generated = generate_adaptive_keyboard_model(profile, contact)
    glb_path = tmp_path / "adaptive_keyboard.glb"
    manifest_path = tmp_path / "adaptive_keyboard_manifest.json"
    glb_path.write_bytes(generated.glb)
    manifest_path.write_text(json.dumps(generated.manifest), encoding="utf-8")
    return glb_path, manifest_path, generated.manifest


def _edit_glb(glb_path: Path, manifest_path: Path, edit):
    payload = glb_path.read_bytes()
    json_size = struct.unpack_from("<I", payload, 12)[0]
    document = json.loads(payload[20 : 20 + json_size])
    binary = payload[20 + json_size :]
    edit(document)
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    rebuilt = (
        struct.pack("<4sII", b"glTF", 2, 20 + len(encoded) + len(binary))
        + struct.pack("<II", len(encoded), 0x4E4F534A)
        + encoded
        + binary
    )
    glb_path.write_bytes(rebuilt)
    manifest = json.loads(manifest_path.read_text())
    manifest["model"]["sha256"] = hashlib.sha256(rebuilt).hexdigest()
    manifest["model"]["byte_length"] = len(rebuilt)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.parametrize("count", [1, 7, 82, 91])
def test_export_supports_actual_user_inventory_not_fixed_82(tmp_path, count):
    glb, manifest_path, manifest = _source_pair(tmp_path, count)
    summary = assets.validate_source_assets(glb, manifest_path)
    assert summary["key_count"] == count
    assert summary["glb_sha256"] == manifest["model"]["sha256"]
    output = tmp_path / "export"
    exported = assets.export_openvr_assets(glb, manifest_path, output)
    assert exported["key_node_count"] == count
    assert exported["source"]["key_count"] == count
    rendered_keys = {
        node
        for component in exported["material_exports"]
        for node in component["source_nodes"]
        if node.startswith("key:")
    }
    assert rendered_keys == {f"key:custom_{index}" for index in range(count)}
    # A reduced custom layout is valid even without accent/function/modifier keys.
    assert len(exported["material_exports"]) < 5
    if count <= 7:
        assert len(exported["material_exports"]) == 2
    assert exported["render_model"]["name"] == "mikotype_keyboard"
    assert exported["render_model"]["filename"] == "mikotype_keyboard.json"


def test_export_is_flat_deterministic_and_integrity_checked(tmp_path):
    glb, manifest, _ = _source_pair(tmp_path)
    source_bytes = (glb.read_bytes(), manifest.read_bytes())
    first = tmp_path / "first"
    second = tmp_path / "second"
    exported = assets.export_openvr_assets(glb, manifest, first)
    assert assets.export_openvr_assets(glb, manifest, second) == exported
    assert (glb.read_bytes(), manifest.read_bytes()) == source_bytes
    assert json.loads((first / assets.EXPORT_MANIFEST_FILENAME).read_text()) == exported
    assert {p.name for p in first.iterdir()} == {
        *exported["outputs"], assets.EXPORT_MANIFEST_FILENAME
    }
    for filename, record in exported["outputs"].items():
        assert Path(filename).name == filename
        assert filename.startswith("mikotype_keyboard")
        payload = (first / filename).read_bytes()
        assert payload == (second / filename).read_bytes()
        assert len(payload) == record["byte_length"]
        assert hashlib.sha256(payload).hexdigest() == record["sha256"]
    description = json.loads((first / assets.RENDER_MODEL_FILENAME).read_text())
    for component in description["components"].values():
        assert component["filename"] in exported["outputs"]
    for component in exported["material_exports"]:
        obj = (first / component["obj"]).read_text()
        assert f"mtllib {component['mtl']}" in obj
        material = (first / component["mtl"]).read_text()
        assert f"map_Kd {component['texture']}" in material


def test_exported_axes_and_bounds_preserve_model_dimensions(tmp_path):
    glb, manifest_path, manifest = _source_pair(tmp_path, 28)
    output = tmp_path / "export"
    exported = assets.export_openvr_assets(glb, manifest_path, output)
    positions = [
        tuple(float(v) for v in line.split()[1:])
        for component in exported["material_exports"]
        for line in (output / component["obj"]).read_text().splitlines()
        if line.startswith("v ")
    ]
    minimum = tuple(min(p[axis] for p in positions) for axis in range(3))
    maximum = tuple(max(p[axis] for p in positions) for axis in range(3))
    assert exported["bounds"]["unit"] == "meters"
    assert exported["bounds"]["valve_obj"]["min"] == pytest.approx(minimum, abs=1e-9)
    assert exported["bounds"]["valve_obj"]["max"] == pytest.approx(maximum, abs=1e-9)
    case_center = manifest["geometry"]["case_center"]
    case_size = manifest["geometry"]["case_size"]
    # Case encompasses key footprints; glTF +Y is height, -Z is OBJ +Y.
    assert maximum[0] - minimum[0] == pytest.approx(case_size[0], abs=1e-8)
    assert maximum[1] - minimum[1] == pytest.approx(case_size[2], abs=1e-8)
    assert minimum[2] == pytest.approx(case_center[1] - case_size[1] / 2, abs=1e-8)
    key_top = max(k["center"][1] + k["size"][1] / 2 for k in manifest["keys"])
    assert maximum[2] == pytest.approx(key_top, abs=1e-8)
    assert exported["axis_policy"]["mapping"] == [
        "x_obj=x_gltf", "y_obj=-z_gltf", "z_obj=y_gltf"
    ]


@pytest.mark.parametrize(
    "edit, expected",
    [
        (lambda m: m.update(key_count=0), "manifest key_count"),
        (lambda m: m.update(key_count=8), "exactly 8 entries"),
        (lambda m: m.update(key_count=True), "manifest key_count"),
        (lambda m: m.update(schema_version="unknown"), "schema_version"),
        (lambda m: m["model"].update(sha256="0" * 64), "SHA-256"),
        (lambda m: m["model"].update(byte_length=1), "byte length"),
        (lambda m: m["keys"][0].update(node_name="key:unknown"), "must bind"),
        (lambda m: m["keys"].__setitem__(1, deepcopy(m["keys"][0])), "unique"),
        (lambda m: m.update(model_revision="different"), "revision"),
    ],
)
def test_invalid_manifest_rejected_without_publishing(tmp_path, edit, expected):
    glb, manifest_path, manifest = _source_pair(tmp_path)
    edit(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "must-not-exist"
    with pytest.raises(assets.AssetExportError, match=expected):
        assets.export_openvr_assets(glb, manifest_path, output)
    assert not output.exists()


def test_missing_key_in_active_scene_is_rejected(tmp_path):
    glb, manifest_path, _ = _source_pair(tmp_path)

    def remove_key(document):
        root = next(n for n in document["nodes"] if n.get("extras", {}).get("role") == "adaptive_keyboard_root")
        root["children"].pop()

    _edit_glb(glb, manifest_path, remove_key)
    with pytest.raises(assets.AssetExportError, match="not every physical-key"):
        assets.validate_source_assets(glb, manifest_path)


def test_double_referenced_node_is_rejected_not_baked_twice(tmp_path):
    glb, manifest_path, _ = _source_pair(tmp_path)

    def duplicate_key(document):
        root = next(n for n in document["nodes"] if n.get("extras", {}).get("role") == "adaptive_keyboard_root")
        root["children"].append(root["children"][-1])

    _edit_glb(glb, manifest_path, duplicate_key)
    with pytest.raises(assets.AssetExportError, match="more than once"):
        assets.validate_source_assets(glb, manifest_path)


def test_exporter_has_no_demo_or_optional_runtime_imports():
    tree = ast.parse(Path(assets.__file__).read_text())
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".")[0])
    assert not imports & {"demo", "openvr", "cv2", "mediapipe", "numpy"}
