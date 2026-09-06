"""Deterministic production-GLB to OpenVR OBJ export checks."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import zlib

import pytest

from demo.steamvr_home_hybrid.asset_export import (
    AXIS_POLICY_ID,
    AssetExportError,
    CONVERTER_VERSION,
    EXPECTED_KEY_COUNT,
    EXPORT_MANIFEST_FILENAME,
    HIGHLIGHT_FILENAME,
    HIGHLIGHT_HEIGHT,
    HIGHLIGHT_WIDTH,
    RENDER_MODEL_FILENAME,
    THUMBNAIL_FILENAME,
    export_openvr_assets,
    validate_source_assets,
)


pytestmark = pytest.mark.integration

REPOSITORY = Path(__file__).resolve().parents[3]
BUNDLE = REPOSITORY / "data" / "keyboards" / "kzzi_user_adjustable_82"
SOURCE_GLB = BUNDLE / "adaptive_keyboard.glb"
SOURCE_MANIFEST = BUNDLE / "adaptive_keyboard_manifest.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _png_rgba(path: Path) -> tuple[int, int, bytes]:
    payload = path.read_bytes()
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    width = height = 0
    compressed = bytearray()
    while offset < len(payload):
        length = struct.unpack_from(">I", payload, offset)[0]
        kind = payload[offset + 4 : offset + 8]
        content = payload[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack_from(">I", payload, offset + 8 + length)[0]
        assert zlib.crc32(kind + content) & 0xFFFFFFFF == expected_crc
        offset += 12 + length
        if kind == b"IHDR":
            width, height, depth, color_type, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", content)
            )
            assert (depth, color_type, compression, filtering, interlace) == (
                8,
                6,
                0,
                0,
                0,
            )
        elif kind == b"IDAT":
            compressed.extend(content)
        elif kind == b"IEND":
            break
    rows = zlib.decompress(bytes(compressed))
    stride = width * 4
    pixels = bytearray()
    for row in range(height):
        start = row * (stride + 1)
        assert rows[start] == 0
        pixels.extend(rows[start + 1 : start + 1 + stride])
    assert len(pixels) == width * height * 4
    return width, height, bytes(pixels)


def _obj_records(path: Path) -> dict[str, list[tuple[float, ...]] | list[str]]:
    positions: list[tuple[float, ...]] = []
    texcoords: list[tuple[float, ...]] = []
    normals: list[tuple[float, ...]] = []
    faces: list[str] = []
    objects: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("v "):
            positions.append(tuple(float(value) for value in line.split()[1:]))
        elif line.startswith("vt "):
            texcoords.append(tuple(float(value) for value in line.split()[1:]))
        elif line.startswith("vn "):
            normals.append(tuple(float(value) for value in line.split()[1:]))
        elif line.startswith("f "):
            faces.append(line)
        elif line.startswith("o "):
            objects.append(line[2:])
    return {
        "positions": positions,
        "texcoords": texcoords,
        "normals": normals,
        "faces": faces,
        "objects": objects,
    }


def test_validate_source_assets_is_strict_and_has_no_side_effects() -> None:
    source = validate_source_assets(SOURCE_GLB, SOURCE_MANIFEST)
    manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))

    assert source["key_count"] == EXPECTED_KEY_COUNT == 82
    assert source["model_revision"] == manifest["model_revision"]
    assert source["geometry_revision"] == manifest["geometry_revision"]
    assert source["node_contract"] == "physical-key-id-v1"
    assert source["glb_sha256"] == _sha256(SOURCE_GLB)
    assert source["manifest_sha256"] == _sha256(SOURCE_MANIFEST)
    assert source["material_count"] == 5
    assert source["vertex_count"] > 82
    assert source["triangle_count"] > 82


def test_export_records_hashes_and_valid_obj_mtl_png(tmp_path: Path) -> None:
    output = tmp_path / "openvr"
    exported = export_openvr_assets(SOURCE_GLB, SOURCE_MANIFEST, output)
    committed = json.loads(
        (output / EXPORT_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )

    assert exported == committed
    assert exported["converter_version"] == CONVERTER_VERSION
    assert exported["export_manifest"]["filename"] == EXPORT_MANIFEST_FILENAME
    assert exported["axis_policy"]["id"] == AXIS_POLICY_ID
    assert exported["key_node_count"] == 82
    assert exported["source"]["glb_sha256"] == _sha256(SOURCE_GLB)
    assert len(exported["material_exports"]) == 5
    assert len(exported["outputs"]) == 18  # 5 × OBJ/MTL/PNG + JSON/thumbnail/highlight

    render_model = json.loads(
        (output / RENDER_MODEL_FILENAME).read_text(encoding="utf-8")
    )
    assert render_model["thumbnail"] == THUMBNAIL_FILENAME
    assert len(render_model["components"]) == 5
    assert set(
        component["filename"] for component in render_model["components"].values()
    ) == {item["obj"] for item in exported["material_exports"]}
    assert exported["render_model"] == {
        "name": "deskvision_keyboard",
        "filename": RENDER_MODEL_FILENAME,
        "thumbnail": THUMBNAIL_FILENAME,
        "component_count": 5,
        "components": {
            item["export_name"]: item["obj"]
            for item in exported["material_exports"]
        },
    }
    rendered_keys = {
        node
        for material in exported["material_exports"]
        for node in material["source_nodes"]
        if node.startswith("key:")
    }
    assert len(rendered_keys) == 82

    for filename, record in exported["outputs"].items():
        path = output / filename
        assert path.is_file()
        assert record["sha256"] == _sha256(path)
        assert record["byte_length"] == path.stat().st_size

    for material in exported["material_exports"]:
        obj_path = output / material["obj"]
        mtl_path = output / material["mtl"]
        texture_path = output / material["texture"]
        records = _obj_records(obj_path)
        positions = records["positions"]
        texcoords = records["texcoords"]
        normals = records["normals"]
        faces = records["faces"]

        assert records["objects"] == [material["export_name"]]
        assert len(positions) == material["vertex_count"]
        assert len(texcoords) == len(positions)
        assert len(normals) == len(positions)
        assert len(faces) == material["triangle_count"]
        assert all(len(value) == 3 and all(math.isfinite(item) for item in value) for value in positions)
        assert all(
            len(value) == 2 and all(0.0 <= item <= 1.0 for item in value)
            for value in texcoords
        )
        assert all(
            len(value) == 3
            and math.isclose(math.sqrt(sum(item * item for item in value)), 1.0, abs_tol=2e-8)
            for value in normals
        )
        face_pattern = re.compile(r"^f (\d+)/(\d+)/(\d+) (\d+)/(\d+)/(\d+) (\d+)/(\d+)/(\d+)$")
        for face in faces:
            match = face_pattern.fullmatch(face)
            assert match is not None
            indices = [int(value) for value in match.groups()]
            assert all(1 <= value <= len(positions) for value in indices)
            assert indices[0] == indices[1] == indices[2]
            assert indices[3] == indices[4] == indices[5]
            assert indices[6] == indices[7] == indices[8]

        mtl = mtl_path.read_text(encoding="utf-8")
        assert f"newmtl {material['export_name']}" in mtl
        assert f"map_Kd {material['texture']}" in mtl
        width, height, pixels = _png_rgba(texture_path)
        assert (width, height) == (4, 4)
        assert len(set(pixels[index : index + 4] for index in range(0, len(pixels), 4))) == 1

    width, height, highlight_pixels = _png_rgba(output / HIGHLIGHT_FILENAME)
    assert (width, height) == (HIGHLIGHT_WIDTH, HIGHLIGHT_HEIGHT)
    assert math.isclose(width / height, 348.5175 / 152.3025, rel_tol=0.002)
    alpha = highlight_pixels[3::4]
    assert min(alpha) == 0
    assert max(alpha) > 200
    assert len(set(alpha)) > 20

    thumbnail_width, thumbnail_height, thumbnail = _png_rgba(
        output / THUMBNAIL_FILENAME
    )
    assert (thumbnail_width, thumbnail_height) == (256, 128)
    assert min(thumbnail[3::4]) == 0
    assert max(thumbnail[3::4]) == 255


def test_export_bakes_node_transforms_and_applies_valve_axis_policy(
    tmp_path: Path,
) -> None:
    output = tmp_path / "openvr"
    exported = export_openvr_assets(SOURCE_GLB, SOURCE_MANIFEST, output)
    source = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))

    case_export = next(
        item for item in exported["material_exports"] if item["source_material_name"] == "case"
    )
    case_positions = _obj_records(output / case_export["obj"])["positions"]
    size_x, size_y, size_z = source["geometry"]["case_size"]
    center_x, center_y, center_z = source["geometry"]["case_center"]
    expected = {
        "x": (center_x - size_x / 2, center_x + size_x / 2),
        "y": (-(center_z + size_z / 2), -(center_z - size_z / 2)),
        "z": (center_y - size_y / 2, center_y + size_y / 2),
    }
    actual = {
        "x": (min(value[0] for value in case_positions), max(value[0] for value in case_positions)),
        "y": (min(value[1] for value in case_positions), max(value[1] for value in case_positions)),
        "z": (min(value[2] for value in case_positions), max(value[2] for value in case_positions)),
    }
    for axis in ("x", "y", "z"):
        assert actual[axis] == pytest.approx(expected[axis], abs=1e-9)

    escape = next(key for key in source["keys"] if key["key_id"] == "escape")
    accent_export = next(
        item for item in exported["material_exports"] if item["source_material_name"] == "accent"
    )
    accent_positions = _obj_records(output / accent_export["obj"])["positions"]
    center_x, center_y, center_z = escape["center"]
    size_x, size_y, size_z = escape["size"]
    top = source["generation_recipe"]["keycap_top_scale"] / 2
    expected_gltf = (
        center_x - top * size_x,
        center_y + 0.5 * size_y,
        center_z + top * size_z,
    )
    expected_valve = (expected_gltf[0], -expected_gltf[2], expected_gltf[1])
    matching_vertices = [
        position
        for position in accent_positions
        if position == pytest.approx(expected_valve, abs=1e-9)
    ]
    assert matching_vertices


def test_export_is_byte_deterministic_across_directories(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    export_openvr_assets(SOURCE_GLB, SOURCE_MANIFEST, first)
    export_openvr_assets(SOURCE_GLB, SOURCE_MANIFEST, second)

    first_files = sorted(path.name for path in first.iterdir())
    second_files = sorted(path.name for path in second.iterdir())
    assert first_files == second_files
    for filename in first_files:
        assert (first / filename).read_bytes() == (second / filename).read_bytes()


@pytest.mark.parametrize("mutation", ["key_count", "model_revision"])
def test_export_rejects_manifest_contract_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    manifest = deepcopy(json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8")))
    if mutation == "key_count":
        manifest["key_count"] = 81
    else:
        manifest["model_revision"] = "f" * 64
    changed_manifest = tmp_path / "manifest.json"
    changed_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "output"

    with pytest.raises(AssetExportError):
        export_openvr_assets(SOURCE_GLB, changed_manifest, output)
    assert not output.exists()


def test_export_rejects_tampered_glb_before_writing_output(tmp_path: Path) -> None:
    payload = bytearray(SOURCE_GLB.read_bytes())
    payload[-1] ^= 0x01
    changed_glb = tmp_path / "tampered.glb"
    changed_glb.write_bytes(payload)
    output = tmp_path / "output"

    with pytest.raises(AssetExportError, match="SHA-256"):
        export_openvr_assets(changed_glb, SOURCE_MANIFEST, output)
    assert not output.exists()
