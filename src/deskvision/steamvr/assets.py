"""Export the production adaptive keyboard into deterministic OpenVR assets.

The production GLB and its sidecar manifest are immutable inputs. This module
validates their revision and physical-key contract, bakes the selected glTF scene
hierarchy, and emits the OBJ/MTL/PNG subset used by the optional OpenVR driver.
It is independent of the demo package and accepts any validated key inventory.

The exporter does not install a SteamVR driver and does not claim that SteamVR
Home is available on the host running it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
from typing import Iterable, Mapping, Sequence
import zlib


EXPORT_SCHEMA_VERSION = "steamvr-home-openvr-asset-export-0.1"
CONVERTER_VERSION = "adaptive-glb-to-openvr-obj-0.3"
EXPECTED_MODEL_SCHEMA = "adaptive-keyboard-3d-model-0.1"
EXPECTED_NODE_CONTRACT = "physical-key-id-v1"
AXIS_POLICY_ID = "gltf-x-y-z-to-valve-obj-x-negz-y-v1"
RENDER_MODEL_FILENAME = "mikotype_keyboard.json"
THUMBNAIL_FILENAME = "mikotype_keyboard_thumbnail.png"
EXPORT_MANIFEST_FILENAME = "export_manifest.json"

_COMPONENT_FORMATS: dict[int, tuple[str, int, bool]] = {
    5120: ("b", 1, True),
    5121: ("B", 1, False),
    5122: ("h", 2, True),
    5123: ("H", 2, False),
    5125: ("I", 4, False),
    5126: ("f", 4, True),
}
_TYPE_WIDTHS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}

Matrix4 = tuple[tuple[float, float, float, float], ...]
Vector2 = tuple[float, float]
Vector3 = tuple[float, float, float]


class AssetExportError(ValueError):
    """The source bundle cannot be exported without weakening its contract."""


@dataclass(frozen=True, slots=True)
class ParsedGlb:
    document: Mapping[str, object]
    binary: bytes


@dataclass(slots=True)
class MaterialGeometry:
    material_index: int
    source_name: str
    export_name: str
    base_color: tuple[float, float, float, float]
    positions: list[Vector3] = field(default_factory=list)
    texcoords: list[Vector2] = field(default_factory=list)
    normals: list[Vector3] = field(default_factory=list)
    faces: list[tuple[int, int, int]] = field(default_factory=list)
    source_nodes: list[str] = field(default_factory=list)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _mapping(value: object, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise AssetExportError(f"{field_name} must be an object")
    return value


def _sequence(value: object, *, field_name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AssetExportError(f"{field_name} must be an array")
    return value


def _finite(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssetExportError(f"{field_name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise AssetExportError(f"{field_name} must be finite")
    return result


def _integer(value: object, *, field_name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AssetExportError(
            f"{field_name} must be an integer greater than or equal to {minimum}"
        )
    return value


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    try:
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AssetExportError("export manifest is not finite JSON") from exc


def _load_manifest(path: Path) -> tuple[Mapping[str, object], bytes]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise AssetExportError(f"cannot read source manifest: {exc}") from exc
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetExportError("source manifest is not valid UTF-8 JSON") from exc
    return _mapping(decoded, field_name="source manifest"), payload


def _parse_glb(payload: bytes) -> ParsedGlb:
    if len(payload) < 20:
        raise AssetExportError("GLB is shorter than its mandatory header")
    magic, version, declared_length = struct.unpack_from("<4sII", payload, 0)
    if magic != b"glTF" or version != 2:
        raise AssetExportError("source model must be a glTF 2.0 binary GLB")
    if declared_length != len(payload):
        raise AssetExportError("GLB declared length does not match file length")

    offset = 12
    chunks: list[tuple[int, bytes]] = []
    while offset < len(payload):
        if offset + 8 > len(payload):
            raise AssetExportError("GLB contains a truncated chunk header")
        chunk_length, chunk_type = struct.unpack_from("<II", payload, offset)
        offset += 8
        end = offset + chunk_length
        if end > len(payload):
            raise AssetExportError("GLB contains a truncated chunk")
        chunks.append((chunk_type, payload[offset:end]))
        offset = end
    if offset != len(payload) or not chunks or chunks[0][0] != 0x4E4F534A:
        raise AssetExportError("GLB must begin with exactly one JSON chunk")
    if sum(chunk_type == 0x4E4F534A for chunk_type, _ in chunks) != 1:
        raise AssetExportError("GLB must contain exactly one JSON chunk")
    unexpected = [kind for kind, _ in chunks[1:] if kind != 0x004E4942]
    binary_chunks = [chunk for kind, chunk in chunks[1:] if kind == 0x004E4942]
    if unexpected or len(binary_chunks) != 1:
        raise AssetExportError("GLB must contain exactly one embedded BIN chunk")
    try:
        document_value = json.loads(chunks[0][1].rstrip(b" \t\r\n\0").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetExportError("GLB JSON chunk is invalid") from exc
    document = _mapping(document_value, field_name="GLB document")
    binary = binary_chunks[0]

    buffers = _sequence(document.get("buffers"), field_name="GLB buffers")
    if len(buffers) != 1:
        raise AssetExportError("only one self-contained GLB buffer is supported")
    buffer = _mapping(buffers[0], field_name="GLB buffers[0]")
    if "uri" in buffer:
        raise AssetExportError("source GLB must not reference an external buffer")
    byte_length = _integer(buffer.get("byteLength"), field_name="buffer byteLength")
    if byte_length > len(binary):
        raise AssetExportError("GLB buffer byteLength exceeds its BIN chunk")
    return ParsedGlb(document=document, binary=binary[:byte_length])


def _validate_source_bundle(
    manifest: Mapping[str, object],
    manifest_bytes: bytes,
    glb_bytes: bytes,
) -> tuple[ParsedGlb, dict[str, object]]:
    if manifest.get("schema_version") != EXPECTED_MODEL_SCHEMA:
        raise AssetExportError(
            f"manifest schema_version must be {EXPECTED_MODEL_SCHEMA}"
        )
    if manifest.get("node_contract") != EXPECTED_NODE_CONTRACT:
        raise AssetExportError(
            f"manifest node_contract must be {EXPECTED_NODE_CONTRACT}"
        )
    model_revision = manifest.get("model_revision")
    if not isinstance(model_revision, str) or not model_revision:
        raise AssetExportError("manifest model_revision must be non-empty")
    geometry_revision = manifest.get("geometry_revision")
    if not isinstance(geometry_revision, str) or not geometry_revision:
        raise AssetExportError("manifest geometry_revision must be non-empty")

    key_count = _integer(
        manifest.get("key_count"), field_name="manifest key_count", minimum=1
    )
    manifest_keys = _sequence(manifest.get("keys"), field_name="manifest keys")
    if len(manifest_keys) != key_count:
        raise AssetExportError(
            f"manifest keys must contain exactly {key_count} entries"
        )
    key_ids: list[str] = []
    expected_nodes: dict[str, str] = {}
    for index, raw_key in enumerate(manifest_keys):
        key = _mapping(raw_key, field_name=f"manifest keys[{index}]")
        key_id = key.get("key_id")
        node_name = key.get("node_name")
        if not isinstance(key_id, str) or not key_id:
            raise AssetExportError(f"manifest keys[{index}].key_id must be non-empty")
        if not isinstance(node_name, str) or node_name != f"key:{key_id}":
            raise AssetExportError(
                f"manifest key {key_id!r} must bind to node key:{key_id}"
            )
        key_ids.append(key_id)
        expected_nodes[key_id] = node_name
    if len(set(key_ids)) != key_count:
        raise AssetExportError("manifest physical key IDs must be unique")

    model = _mapping(manifest.get("model"), field_name="manifest model")
    expected_hash = model.get("sha256")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
        raise AssetExportError("manifest model.sha256 is invalid")
    actual_hash = _sha256(glb_bytes)
    if actual_hash != expected_hash:
        raise AssetExportError("source GLB SHA-256 does not match the manifest")
    if _integer(model.get("byte_length"), field_name="manifest model.byte_length") != len(
        glb_bytes
    ):
        raise AssetExportError("source GLB byte length does not match the manifest")
    if model.get("self_contained") is not True or model.get("external_asset_used") is not False:
        raise AssetExportError("manifest must declare a self-contained source GLB")

    parsed = _parse_glb(glb_bytes)
    document = parsed.document
    asset = _mapping(document.get("asset"), field_name="GLB asset")
    if asset.get("version") != "2.0":
        raise AssetExportError("GLB asset.version must be 2.0")
    asset_extras = _mapping(asset.get("extras"), field_name="GLB asset.extras")
    if asset_extras.get("model_revision") != model_revision:
        raise AssetExportError("GLB asset revision does not match the manifest")

    nodes = _sequence(document.get("nodes"), field_name="GLB nodes")
    root_indices: list[int] = []
    key_nodes: dict[str, tuple[int, Mapping[str, object]]] = {}
    for index, raw_node in enumerate(nodes):
        node = _mapping(raw_node, field_name=f"GLB nodes[{index}]")
        extras_value = node.get("extras", {})
        extras = _mapping(extras_value, field_name=f"GLB nodes[{index}].extras")
        role = extras.get("role")
        if role == "adaptive_keyboard_root":
            root_indices.append(index)
            if extras.get("model_revision") != model_revision:
                raise AssetExportError("adaptive keyboard root revision mismatch")
            if extras.get("node_contract") != EXPECTED_NODE_CONTRACT:
                raise AssetExportError("adaptive keyboard root node contract mismatch")
        elif role == "keyboard_key":
            key_id = extras.get("key_id")
            if not isinstance(key_id, str) or key_id not in expected_nodes:
                raise AssetExportError("GLB contains an unknown physical keyboard key node")
            if key_id in key_nodes:
                raise AssetExportError(f"GLB contains duplicate keyboard key {key_id!r}")
            if node.get("name") != expected_nodes[key_id]:
                raise AssetExportError(f"GLB keyboard key node name mismatch for {key_id}")
            if extras.get("model_revision") != model_revision:
                raise AssetExportError(f"GLB keyboard key revision mismatch for {key_id}")
            if "mesh" not in node:
                raise AssetExportError(f"GLB keyboard key {key_id} has no renderable mesh")
            key_nodes[key_id] = (index, node)
    if len(root_indices) != 1:
        raise AssetExportError("GLB must contain exactly one adaptive keyboard root")
    if set(key_nodes) != set(expected_nodes):
        missing = sorted(set(expected_nodes) - set(key_nodes))
        raise AssetExportError(
            "GLB physical-key nodes do not match the manifest"
            + (f": missing {missing[0]}" if missing else "")
        )

    reachable = set(index for index, _, _ in _walk_scene(document))
    if root_indices[0] not in reachable:
        raise AssetExportError("adaptive keyboard root is not reachable from the active scene")
    if any(index not in reachable for index, _ in key_nodes.values()):
        raise AssetExportError("not every physical-key node is reachable from the active scene")

    source = {
        "glb_filename": str(model.get("filename", "adaptive_keyboard.glb")),
        "glb_sha256": actual_hash,
        "glb_byte_length": len(glb_bytes),
        "manifest_filename": "adaptive_keyboard_manifest.json",
        "manifest_sha256": _sha256(manifest_bytes),
        "model_revision": model_revision,
        "geometry_revision": geometry_revision,
        "node_contract": EXPECTED_NODE_CONTRACT,
        "key_count": key_count,
    }
    return parsed, source


def validate_source_assets(
    source_glb: str | Path,
    source_manifest: str | Path,
) -> dict[str, object]:
    """Strictly validate the immutable production source pair.

    The returned summary is path-independent and can be passed directly to a
    service status API.  No output directory is created by this function.
    """

    glb_path = Path(source_glb)
    manifest_path = Path(source_manifest)
    try:
        glb_bytes = glb_path.read_bytes()
    except OSError as exc:
        raise AssetExportError(f"cannot read source GLB: {exc}") from exc
    manifest, manifest_bytes = _load_manifest(manifest_path)
    parsed, source_metadata = _validate_source_bundle(
        manifest, manifest_bytes, glb_bytes
    )
    groups, _ = _bake_geometry(parsed, key_count=int(source_metadata["key_count"]))
    return {
        **source_metadata,
        "material_count": len(groups),
        "vertex_count": sum(len(group.positions) for group in groups),
        "triangle_count": sum(len(group.faces) for group in groups),
    }


def _identity() -> Matrix4:
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _multiply(left: Matrix4, right: Matrix4) -> Matrix4:
    return tuple(
        tuple(sum(left[row][inner] * right[inner][column] for inner in range(4)) for column in range(4))
        for row in range(4)
    )


def _node_matrix(node: Mapping[str, object], *, node_index: int) -> Matrix4:
    if "matrix" in node:
        if any(field in node for field in ("translation", "rotation", "scale")):
            raise AssetExportError(
                f"GLB node {node_index} cannot combine matrix with TRS fields"
            )
        values = _sequence(node["matrix"], field_name=f"GLB nodes[{node_index}].matrix")
        if len(values) != 16:
            raise AssetExportError(f"GLB node {node_index} matrix must contain 16 values")
        parsed = [
            _finite(value, field_name=f"GLB nodes[{node_index}].matrix")
            for value in values
        ]
        matrix = tuple(
            tuple(parsed[column * 4 + row] for column in range(4))
            for row in range(4)
        )
        if any(abs(matrix[3][index] - expected) > 1e-9 for index, expected in enumerate((0.0, 0.0, 0.0, 1.0))):
            raise AssetExportError(f"GLB node {node_index} matrix must be affine")
        return matrix

    translation = _parse_vector(node.get("translation", (0.0, 0.0, 0.0)), 3, f"GLB nodes[{node_index}].translation")
    rotation = _parse_vector(node.get("rotation", (0.0, 0.0, 0.0, 1.0)), 4, f"GLB nodes[{node_index}].rotation")
    scale = _parse_vector(node.get("scale", (1.0, 1.0, 1.0)), 3, f"GLB nodes[{node_index}].scale")
    if any(abs(value) <= 1e-12 for value in scale):
        raise AssetExportError(f"GLB node {node_index} has a singular scale")
    x, y, z, w = rotation
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length <= 1e-12:
        raise AssetExportError(f"GLB node {node_index} has an invalid quaternion")
    x, y, z, w = (value / length for value in (x, y, z, w))
    rotation_matrix: Matrix4 = (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0.0),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0.0),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    scale_matrix: Matrix4 = (
        (scale[0], 0.0, 0.0, 0.0),
        (0.0, scale[1], 0.0, 0.0),
        (0.0, 0.0, scale[2], 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    translation_matrix: Matrix4 = (
        (1.0, 0.0, 0.0, translation[0]),
        (0.0, 1.0, 0.0, translation[1]),
        (0.0, 0.0, 1.0, translation[2]),
        (0.0, 0.0, 0.0, 1.0),
    )
    return _multiply(translation_matrix, _multiply(rotation_matrix, scale_matrix))


def _parse_vector(value: object, width: int, field_name: str) -> tuple[float, ...]:
    values = _sequence(value, field_name=field_name)
    if len(values) != width:
        raise AssetExportError(f"{field_name} must contain {width} values")
    return tuple(_finite(item, field_name=field_name) for item in values)


def _walk_scene(
    document: Mapping[str, object],
) -> Iterable[tuple[int, Mapping[str, object], Matrix4]]:
    nodes = _sequence(document.get("nodes"), field_name="GLB nodes")
    scenes = _sequence(document.get("scenes"), field_name="GLB scenes")
    scene_index = _integer(document.get("scene", 0), field_name="GLB scene")
    if scene_index >= len(scenes):
        raise AssetExportError("GLB scene index is out of range")
    scene = _mapping(scenes[scene_index], field_name=f"GLB scenes[{scene_index}]")
    roots = _sequence(scene.get("nodes", ()), field_name="active scene nodes")
    if not roots:
        raise AssetExportError("active GLB scene has no root nodes")

    visited: set[int] = set()

    def visit(
        raw_index: object,
        parent: Matrix4,
        stack: tuple[int, ...],
    ) -> Iterable[tuple[int, Mapping[str, object], Matrix4]]:
        node_index = _integer(raw_index, field_name="GLB node index")
        if node_index >= len(nodes):
            raise AssetExportError("GLB node index is out of range")
        if node_index in stack:
            raise AssetExportError("GLB scene graph contains a cycle")
        if node_index in visited:
            raise AssetExportError("GLB scene references one node more than once")
        visited.add(node_index)
        node = _mapping(nodes[node_index], field_name=f"GLB nodes[{node_index}]")
        world = _multiply(parent, _node_matrix(node, node_index=node_index))
        yield node_index, node, world
        children = _sequence(
            node.get("children", ()), field_name=f"GLB nodes[{node_index}].children"
        )
        for child in children:
            yield from visit(child, world, (*stack, node_index))

    for root in roots:
        yield from visit(root, _identity(), ())


def _read_accessor(
    document: Mapping[str, object],
    binary: bytes,
    accessor_index: object,
    *,
    field_name: str,
) -> tuple[str, int, list[tuple[float | int, ...]]]:
    accessors = _sequence(document.get("accessors"), field_name="GLB accessors")
    index = _integer(accessor_index, field_name=f"{field_name} accessor index")
    if index >= len(accessors):
        raise AssetExportError(f"{field_name} accessor index is out of range")
    accessor = _mapping(accessors[index], field_name=f"GLB accessors[{index}]")
    if "sparse" in accessor:
        raise AssetExportError("sparse glTF accessors are not supported by this exporter")
    component_type = _integer(
        accessor.get("componentType"), field_name=f"GLB accessors[{index}].componentType"
    )
    if component_type not in _COMPONENT_FORMATS:
        raise AssetExportError(f"GLB accessor {index} has an unsupported component type")
    element_type = accessor.get("type")
    if element_type not in _TYPE_WIDTHS:
        raise AssetExportError(f"GLB accessor {index} has an unsupported element type")
    width = _TYPE_WIDTHS[str(element_type)]
    count = _integer(accessor.get("count"), field_name=f"GLB accessors[{index}].count")
    if count <= 0:
        raise AssetExportError(f"GLB accessor {index} must not be empty")
    view_index = _integer(
        accessor.get("bufferView"), field_name=f"GLB accessors[{index}].bufferView"
    )
    views = _sequence(document.get("bufferViews"), field_name="GLB bufferViews")
    if view_index >= len(views):
        raise AssetExportError(f"GLB accessor {index} bufferView is out of range")
    view = _mapping(views[view_index], field_name=f"GLB bufferViews[{view_index}]")
    if _integer(view.get("buffer", 0), field_name="bufferView buffer") != 0:
        raise AssetExportError("only the embedded GLB buffer is supported")
    format_code, component_size, signed = _COMPONENT_FORMATS[component_type]
    element_size = component_size * width
    stride = _integer(view.get("byteStride", element_size), field_name="bufferView byteStride")
    if stride < element_size:
        raise AssetExportError(f"GLB accessor {index} byteStride is too small")
    view_offset = _integer(view.get("byteOffset", 0), field_name="bufferView byteOffset")
    view_length = _integer(view.get("byteLength"), field_name="bufferView byteLength")
    accessor_offset = _integer(
        accessor.get("byteOffset", 0), field_name=f"GLB accessors[{index}].byteOffset"
    )
    start = view_offset + accessor_offset
    end = start + (count - 1) * stride + element_size
    if end > view_offset + view_length or end > len(binary):
        raise AssetExportError(f"GLB accessor {index} exceeds its bufferView")
    unpacker = struct.Struct("<" + format_code * width)
    rows: list[tuple[float | int, ...]] = []
    normalized = accessor.get("normalized", False)
    if not isinstance(normalized, bool):
        raise AssetExportError(f"GLB accessor {index} normalized must be boolean")
    for row_index in range(count):
        values = unpacker.unpack_from(binary, start + row_index * stride)
        if normalized and component_type != 5126:
            bits = component_size * 8
            denominator = (2 ** (bits - 1) - 1) if signed else (2**bits - 1)
            if signed:
                rows.append(tuple(max(-1.0, value / denominator) for value in values))
            else:
                rows.append(tuple(value / denominator for value in values))
        else:
            rows.append(tuple(values))
    return str(element_type), component_type, rows


def _transform_position(matrix: Matrix4, value: Sequence[float | int]) -> Vector3:
    x, y, z = (float(value[index]) for index in range(3))
    output = tuple(
        matrix[row][0] * x
        + matrix[row][1] * y
        + matrix[row][2] * z
        + matrix[row][3]
        for row in range(4)
    )
    if abs(output[3]) <= 1e-12:
        raise AssetExportError("a baked vertex transformed to infinity")
    result = tuple(output[index] / output[3] for index in range(3))
    if not all(math.isfinite(item) for item in result):
        raise AssetExportError("a baked vertex is not finite")
    return result  # type: ignore[return-value]


def _determinant3(matrix: Matrix4) -> float:
    a, b, c = matrix[0][:3]
    d, e, f = matrix[1][:3]
    g, h, i = matrix[2][:3]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def _transform_normal(matrix: Matrix4, value: Sequence[float | int]) -> Vector3:
    a, b, c = matrix[0][:3]
    d, e, f = matrix[1][:3]
    g, h, i = matrix[2][:3]
    determinant = _determinant3(matrix)
    if abs(determinant) <= 1e-15:
        raise AssetExportError("a baked node transform is singular")
    inverse = (
        ((e * i - f * h) / determinant, (c * h - b * i) / determinant, (b * f - c * e) / determinant),
        ((f * g - d * i) / determinant, (a * i - c * g) / determinant, (c * d - a * f) / determinant),
        ((d * h - e * g) / determinant, (b * g - a * h) / determinant, (a * e - b * d) / determinant),
    )
    x, y, z = (float(value[index]) for index in range(3))
    # inverse-transpose(world_linear) * normal
    transformed = (
        inverse[0][0] * x + inverse[1][0] * y + inverse[2][0] * z,
        inverse[0][1] * x + inverse[1][1] * y + inverse[2][1] * z,
        inverse[0][2] * x + inverse[1][2] * y + inverse[2][2] * z,
    )
    length = math.sqrt(sum(item * item for item in transformed))
    if length <= 1e-15 or not math.isfinite(length):
        raise AssetExportError("a baked normal is degenerate")
    return tuple(item / length for item in transformed)  # type: ignore[return-value]


def _to_valve(value: Vector3) -> Vector3:
    """Map right-handed glTF +Y-up coordinates to Valve OBJ +Z-up."""

    return (value[0], -value[2], value[1])


def _bounds(values: Sequence[Sequence[float | int]]) -> tuple[Vector3, Vector3]:
    minimum = tuple(min(float(value[axis]) for value in values) for axis in range(3))
    maximum = tuple(max(float(value[axis]) for value in values) for axis in range(3))
    return minimum, maximum  # type: ignore[return-value]


def _normalized_axis(value: float, minimum: float, maximum: float) -> float:
    span = maximum - minimum
    if abs(span) <= 1e-12:
        return 0.5
    return max(0.0, min(1.0, (value - minimum) / span))


def _synthesize_texcoords(
    positions: Sequence[Sequence[float | int]],
    normals: Sequence[Sequence[float | int]],
) -> list[Vector2]:
    minimum, maximum = _bounds(positions)
    result: list[Vector2] = []
    for position, normal in zip(positions, normals, strict=True):
        x, y, z = (float(position[index]) for index in range(3))
        nx, ny, nz = (abs(float(normal[index])) for index in range(3))
        if ny >= nx and ny >= nz:
            uv = (
                _normalized_axis(x, minimum[0], maximum[0]),
                _normalized_axis(z, minimum[2], maximum[2]),
            )
        elif nx >= nz:
            uv = (
                _normalized_axis(z, minimum[2], maximum[2]),
                _normalized_axis(y, minimum[1], maximum[1]),
            )
        else:
            uv = (
                _normalized_axis(x, minimum[0], maximum[0]),
                _normalized_axis(y, minimum[1], maximum[1]),
            )
        result.append(uv)
    return result


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized or "material"


def _material_groups(parsed: ParsedGlb) -> tuple[dict[int, MaterialGeometry], list[dict[str, object]]]:
    document = parsed.document
    materials = _sequence(document.get("materials"), field_name="GLB materials")
    if not materials:
        raise AssetExportError("GLB contains no materials")
    groups: dict[int, MaterialGeometry] = {}
    material_metadata: list[dict[str, object]] = []
    for index, raw_material in enumerate(materials):
        material = _mapping(raw_material, field_name=f"GLB materials[{index}]")
        pbr = _mapping(
            material.get("pbrMetallicRoughness", {}),
            field_name=f"GLB materials[{index}].pbrMetallicRoughness",
        )
        if "baseColorTexture" in pbr:
            raise AssetExportError(
                "source base-color textures are not supported by the deterministic converter"
            )
        color_values = _parse_vector(
            pbr.get("baseColorFactor", (1.0, 1.0, 1.0, 1.0)),
            4,
            f"GLB materials[{index}].baseColorFactor",
        )
        if any(not 0.0 <= value <= 1.0 for value in color_values):
            raise AssetExportError("GLB base-color factors must be between zero and one")
        source_name = str(material.get("name", f"material_{index:02d}"))
        export_name = f"mikotype_keyboard_material_{index:02d}_{_slug(source_name)}"
        groups[index] = MaterialGeometry(
            material_index=index,
            source_name=source_name,
            export_name=export_name,
            base_color=(color_values[0], color_values[1], color_values[2], color_values[3]),
        )
        material_metadata.append(
            {
                "source_material_index": index,
                "source_material_name": source_name,
                "export_name": export_name,
                "base_color_factor": list(color_values),
            }
        )
    return groups, material_metadata


def _bake_geometry(parsed: ParsedGlb, *, key_count: int) -> tuple[list[MaterialGeometry], list[dict[str, object]]]:
    document = parsed.document
    meshes = _sequence(document.get("meshes"), field_name="GLB meshes")
    groups, material_metadata = _material_groups(parsed)
    used_materials: set[int] = set()
    rendered_key_nodes: set[str] = set()
    for node_index, node, world in _walk_scene(document):
        if "mesh" not in node:
            continue
        mesh_index = _integer(node["mesh"], field_name=f"GLB nodes[{node_index}].mesh")
        if mesh_index >= len(meshes):
            raise AssetExportError(f"GLB node {node_index} mesh index is out of range")
        mesh = _mapping(meshes[mesh_index], field_name=f"GLB meshes[{mesh_index}]")
        primitives = _sequence(
            mesh.get("primitives"), field_name=f"GLB meshes[{mesh_index}].primitives"
        )
        node_name = str(node.get("name", f"node_{node_index}"))
        extras = _mapping(
            node.get("extras", {}), field_name=f"GLB nodes[{node_index}].extras"
        )
        is_keyboard_key = extras.get("role") == "keyboard_key"
        rendered_primitive = False
        for primitive_index, raw_primitive in enumerate(primitives):
            primitive = _mapping(
                raw_primitive,
                field_name=f"GLB meshes[{mesh_index}].primitives[{primitive_index}]",
            )
            if _integer(primitive.get("mode", 4), field_name="primitive mode") != 4:
                raise AssetExportError("only TRIANGLES glTF primitives can be exported")
            attributes = _mapping(primitive.get("attributes"), field_name="primitive attributes")
            position_type, position_component, raw_positions = _read_accessor(
                document,
                parsed.binary,
                attributes.get("POSITION"),
                field_name="POSITION",
            )
            normal_type, normal_component, raw_normals = _read_accessor(
                document,
                parsed.binary,
                attributes.get("NORMAL"),
                field_name="NORMAL",
            )
            if (position_type, position_component) != ("VEC3", 5126):
                raise AssetExportError("POSITION must be a float VEC3 accessor")
            if (normal_type, normal_component) != ("VEC3", 5126):
                raise AssetExportError("NORMAL must be a float VEC3 accessor")
            if len(raw_positions) != len(raw_normals):
                raise AssetExportError("POSITION and NORMAL accessor lengths differ")
            if "TEXCOORD_0" in attributes:
                uv_type, _, raw_uvs = _read_accessor(
                    document,
                    parsed.binary,
                    attributes["TEXCOORD_0"],
                    field_name="TEXCOORD_0",
                )
                if uv_type != "VEC2" or len(raw_uvs) != len(raw_positions):
                    raise AssetExportError("TEXCOORD_0 must be a matching VEC2 accessor")
                texcoords = [
                    (
                        _finite(row[0], field_name="TEXCOORD_0 u"),
                        _finite(row[1], field_name="TEXCOORD_0 v"),
                    )
                    for row in raw_uvs
                ]
            else:
                texcoords = _synthesize_texcoords(raw_positions, raw_normals)

            index_type, index_component, raw_indices = _read_accessor(
                document,
                parsed.binary,
                primitive.get("indices"),
                field_name="indices",
            )
            if index_type != "SCALAR" or index_component not in {5121, 5123, 5125}:
                raise AssetExportError("indices must use an unsigned integer SCALAR accessor")
            indices = [int(row[0]) for row in raw_indices]
            if len(indices) % 3:
                raise AssetExportError("triangle index count must be divisible by three")
            if any(index < 0 or index >= len(raw_positions) for index in indices):
                raise AssetExportError("triangle index is outside the POSITION accessor")

            material_index = _integer(
                primitive.get("material"), field_name="primitive material index"
            )
            if material_index not in groups:
                raise AssetExportError("primitive material index is out of range")
            group = groups[material_index]
            used_materials.add(material_index)
            rendered_primitive = True
            if node_name not in group.source_nodes:
                group.source_nodes.append(node_name)
            base = len(group.positions)
            group.positions.extend(
                _to_valve(_transform_position(world, position))
                for position in raw_positions
            )
            group.normals.extend(
                _to_valve(_transform_normal(world, normal)) for normal in raw_normals
            )
            group.texcoords.extend(texcoords)
            flip_winding = _determinant3(world) < 0
            for offset in range(0, len(indices), 3):
                triangle = indices[offset : offset + 3]
                if flip_winding:
                    triangle[1], triangle[2] = triangle[2], triangle[1]
                group.faces.append(tuple(base + index for index in triangle))
        if is_keyboard_key and rendered_primitive:
            rendered_key_nodes.add(node_name)

    if not used_materials:
        raise AssetExportError("active GLB scene contains no renderable primitives")
    if len(rendered_key_nodes) != key_count:
        raise AssetExportError(
            f"active GLB scene must render all {key_count} physical key nodes"
        )
    # Small/custom keyboards need not use every semantic material recipe.
    return [groups[index] for index in sorted(used_materials)], [
        item for item in material_metadata if item["source_material_index"] in used_materials
    ]


def _format_float(value: float) -> str:
    if not math.isfinite(value):
        raise AssetExportError("OBJ/MTL output cannot contain non-finite values")
    if abs(value) < 0.0000000005:
        value = 0.0
    rendered = f"{value:.9f}".rstrip("0").rstrip(".")
    return rendered if rendered not in {"", "-0"} else "0"


def _obj_bytes(group: MaterialGeometry, *, mtl_filename: str) -> bytes:
    lines = [
        "# Deterministic OpenVR render-model geometry",
        f"# converter {CONVERTER_VERSION}",
        f"mtllib {mtl_filename}",
        f"o {group.export_name}",
    ]
    lines.extend("v " + " ".join(_format_float(item) for item in value) for value in group.positions)
    lines.extend("vt " + " ".join(_format_float(item) for item in value) for value in group.texcoords)
    lines.extend("vn " + " ".join(_format_float(item) for item in value) for value in group.normals)
    lines.extend((f"usemtl {group.export_name}", "s off"))
    for first, second, third in group.faces:
        # Every emitted vertex has a corresponding synthesized/source UV and
        # inverse-transpose transformed normal, so all three OBJ indices match.
        values = (first + 1, second + 1, third + 1)
        lines.append("f " + " ".join(f"{value}/{value}/{value}" for value in values))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _mtl_bytes(group: MaterialGeometry, *, texture_filename: str) -> bytes:
    red, green, blue, alpha = group.base_color
    lines = (
        "# Deterministic OpenVR render-model material",
        f"# converter {CONVERTER_VERSION}",
        f"newmtl {group.export_name}",
        "Ka 0 0 0",
        "Kd " + " ".join(_format_float(value) for value in (red, green, blue)),
        "Ks 0 0 0",
        "Ns 1",
        f"d {_format_float(alpha)}",
        "illum 2",
        f"map_Kd {texture_filename}",
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(
        ">I", zlib.crc32(kind + payload) & 0xFFFFFFFF
    )


def _rgba_png(width: int, height: int, pixels: bytes) -> bytes:
    if width <= 0 or height <= 0 or len(pixels) != width * height * 4:
        raise AssetExportError("invalid RGBA pixels for PNG output")
    scanlines = b"".join(
        b"\0" + pixels[row * width * 4 : (row + 1) * width * 4]
        for row in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", header),
            _png_chunk(b"IDAT", zlib.compress(scanlines, level=9)),
            _png_chunk(b"IEND", b""),
        )
    )


def _solid_texture(color: tuple[float, float, float, float]) -> bytes:
    rgba = bytes(max(0, min(255, round(channel * 255))) for channel in color)
    return _rgba_png(4, 4, rgba * 16)


def _thumbnail_texture() -> bytes:
    """Create a small, deterministic keyboard silhouette for OpenVR tools."""

    width, height = 256, 128
    pixels = bytearray(width * height * 4)

    def rectangle(
        left: int,
        top: int,
        right: int,
        bottom: int,
        color: tuple[int, int, int, int],
    ) -> None:
        for row in range(max(0, top), min(height, bottom)):
            for column in range(max(0, left), min(width, right)):
                offset = (row * width + column) * 4
                pixels[offset : offset + 4] = bytes(color)

    rectangle(8, 20, 248, 108, (14, 22, 29, 255))
    rectangle(13, 25, 243, 103, (38, 51, 57, 255))
    for row in range(5):
        top = 30 + row * 14
        columns = 15 if row < 4 else 10
        for column in range(columns):
            left = 18 + column * 14
            key_width = 11
            if row == 4 and column == 3:
                key_width = 53
            if row == 4 and column > 3:
                left += 42
            color = (217, 205, 172, 255)
            if row == 0:
                color = (101, 114, 91, 255)
            rectangle(left, top, left + key_width, top + 10, color)
    rectangle(229, 72, 238, 81, (85, 255, 189, 235))
    return _rgba_png(width, height, bytes(pixels))


def _render_model_description(groups: Sequence[MaterialGeometry]) -> dict[str, object]:
    return {
        "thumbnail": THUMBNAIL_FILENAME,
        "components": {
            group.export_name: {"filename": f"{group.export_name}.obj"}
            for group in groups
        },
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.tmp-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        os.replace(temporary, path)
    except OSError as exc:
        try:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise AssetExportError(f"cannot publish {path.name}: {exc}") from exc


def export_openvr_assets(
    source_glb: str | Path,
    source_manifest: str | Path,
    output_directory: str | Path,
) -> dict[str, object]:
    """Validate and export one immutable adaptive keyboard bundle.

    ``export_manifest.json`` is written last and acts as the commit record for
    the OBJ/MTL/PNG set.  The manifest intentionally records filenames rather
    than host-specific absolute paths so two exports are byte-for-byte equal.
    """

    glb_path = Path(source_glb)
    manifest_path = Path(source_manifest)
    output_path = Path(output_directory)
    try:
        glb_bytes = glb_path.read_bytes()
    except OSError as exc:
        raise AssetExportError(f"cannot read source GLB: {exc}") from exc
    manifest, manifest_bytes = _load_manifest(manifest_path)
    parsed, source_metadata = _validate_source_bundle(
        manifest, manifest_bytes, glb_bytes
    )
    groups, material_metadata = _bake_geometry(parsed, key_count=int(source_metadata["key_count"]))

    files: dict[str, tuple[str, bytes]] = {}
    material_exports: list[dict[str, object]] = []
    metadata_by_index = {
        int(item["source_material_index"]): item for item in material_metadata
    }
    for group in groups:
        if len(group.positions) > 65_000:
            raise AssetExportError(
                f"OpenVR OBJ {group.export_name} exceeds the 65,000-vertex limit"
            )
        stem = group.export_name
        obj_filename = f"{stem}.obj"
        mtl_filename = f"{stem}.mtl"
        texture_filename = f"{stem}.png"
        files[obj_filename] = ("obj", _obj_bytes(group, mtl_filename=mtl_filename))
        files[mtl_filename] = (
            "mtl",
            _mtl_bytes(group, texture_filename=texture_filename),
        )
        files[texture_filename] = ("material_texture", _solid_texture(group.base_color))
        material_exports.append(
            {
                **metadata_by_index[group.material_index],
                "obj": obj_filename,
                "mtl": mtl_filename,
                "texture": texture_filename,
                "object_count": 1,
                "vertex_count": len(group.positions),
                "triangle_count": len(group.faces),
                "source_nodes": list(group.source_nodes),
            }
        )
    render_model = _render_model_description(groups)
    files[RENDER_MODEL_FILENAME] = (
        "openvr_render_model_description",
        _json_bytes(render_model),
    )
    files[THUMBNAIL_FILENAME] = ("openvr_thumbnail", _thumbnail_texture())

    output_records = {
        filename: {
            "kind": kind,
            "sha256": _sha256(payload),
            "byte_length": len(payload),
        }
        for filename, (kind, payload) in sorted(files.items())
    }
    bounds_min, bounds_max = _bounds(
        [position for group in groups for position in group.positions]
    )
    export_manifest: dict[str, object] = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "converter_version": CONVERTER_VERSION,
        "export_manifest": {"filename": EXPORT_MANIFEST_FILENAME},
        "source": source_metadata,
        "axis_policy": {
            "id": AXIS_POLICY_ID,
            "source": "glTF right-handed +Y up, meters",
            "target": "Valve/OpenVR OBJ right-handed +Z up, meters",
            "mapping": ["x_obj=x_gltf", "y_obj=-z_gltf", "z_obj=y_gltf"],
            "node_transforms": "selected-scene hierarchy baked into vertices and normals",
        },
        "key_node_count": source_metadata["key_count"],
        "bounds": {
            "unit": "meters",
            "valve_obj": {
                "min": list(bounds_min),
                "max": list(bounds_max),
            },
        },
        "material_exports": material_exports,
        "render_model": {
            "name": "mikotype_keyboard",
            "filename": RENDER_MODEL_FILENAME,
            "thumbnail": THUMBNAIL_FILENAME,
            "component_count": len(groups),
            "components": {
                group.export_name: f"{group.export_name}.obj" for group in groups
            },
        },
        "outputs": output_records,
    }

    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AssetExportError(f"cannot create output directory: {exc}") from exc
    if not output_path.is_dir():
        raise AssetExportError("output directory is not a directory")
    for filename, (_, payload) in sorted(files.items()):
        _atomic_write(output_path / filename, payload)
    _atomic_write(output_path / EXPORT_MANIFEST_FILENAME, _json_bytes(export_manifest))
    return export_manifest



__all__ = [
    "AXIS_POLICY_ID",
    "AssetExportError",
    "CONVERTER_VERSION",
    "EXPORT_MANIFEST_FILENAME",
    "EXPORT_SCHEMA_VERSION",
    "RENDER_MODEL_FILENAME",
    "THUMBNAIL_FILENAME",
    "export_openvr_assets",
    "validate_source_assets",
]
