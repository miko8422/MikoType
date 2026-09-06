"""Generate a self-contained adaptive keyboard GLB from validated artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import struct
from typing import Mapping, Sequence

from deskvision.calibration.contact_map import KeyboardContactMap
from deskvision.calibration.layout_profile import KeyboardLayoutProfile


MODEL_SCHEMA_VERSION = "adaptive-keyboard-3d-model-0.1"
GENERATOR_VERSION = "procedural-keyboard-glb-0.1"
GLB_FILENAME = "adaptive_keyboard.glb"
NODE_CONTRACT = "physical-key-id-v1"

KEY_PITCH_M = 0.01905
KEY_GAP_M = 0.0022
KEY_HEIGHT_M = 0.0105
KEY_CLEARANCE_M = 0.001
KEYCAP_TOP_SCALE = 0.88
MIN_KEYCAP_SIZE_M = 0.004
CASE_MARGIN_M = 0.009
CASE_HEIGHT_M = 0.008
MAX_LAYOUT_EXTENT_UNITS = 1000.0
MATERIAL_POLICY_VERSION = "semantic-key-groups-v1"

MATERIAL_RECIPES = (
    ("case", (0.055, 0.075, 0.09, 1.0), 0.18, 0.56),
    ("standard", (0.82, 0.79, 0.67, 1.0), 0.0, 0.64),
    ("function", (0.37, 0.42, 0.34, 1.0), 0.0, 0.66),
    ("modifier", (0.27, 0.34, 0.32, 1.0), 0.0, 0.66),
    ("accent", (0.85, 0.35, 0.10, 1.0), 0.0, 0.58),
)


class AdaptiveKeyboardModelError(ValueError):
    """The supplied production artifacts cannot form one coherent model."""


@dataclass(frozen=True, slots=True)
class GeneratedKeyboardModel:
    """A generated binary glTF and its exact JSON-ready sidecar Manifest."""

    manifest: dict[str, object]
    glb: bytes


def _canonical_hash(payload: object) -> str:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AdaptiveKeyboardModelError("revision payload is not finite JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdaptiveKeyboardModelError(f"{field} must be numeric")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise AdaptiveKeyboardModelError(f"{field} must be finite") from exc
    if not math.isfinite(result):
        raise AdaptiveKeyboardModelError(f"{field} must be finite")
    return result


def layout_geometry_revision(profile: KeyboardLayoutProfile) -> str:
    """Hash every user-controlled field represented in the generated GLB."""

    return _canonical_hash(
        {
            "schema_version": "adaptive-keyboard-layout-geometry-0.1",
            "layout_id": profile.layout_id,
            "unit": profile.unit,
            "keys": [
                {
                    "key_id": key.key_id,
                    "label": key.label,
                    "browser_code": key.browser_code,
                    "row": key.row,
                    "x_units": _finite_number(
                        key.x_units, field=f"{key.key_id}.x_units"
                    ),
                    "y_units": _finite_number(
                        key.y_units, field=f"{key.key_id}.y_units"
                    ),
                    "width_units": _finite_number(
                        key.width_units, field=f"{key.key_id}.width_units"
                    ),
                    "height_units": _finite_number(
                        key.height_units, field=f"{key.key_id}.height_units"
                    ),
                }
                for key in profile.keys
            ],
        }
    )


def _material_style(key_id: str, row: int) -> str:
    if key_id in {
        "escape",
        "delete_top_right",
        "arrow_left",
        "arrow_down",
        "arrow_right",
        "arrow_up",
    }:
        return "accent"
    if row == 0:
        return "function"
    if key_id in {
        "tab",
        "caps_lock",
        "enter",
        "backspace",
        "left_shift",
        "right_shift",
        "left_control",
        "right_control",
        "left_meta",
        "left_alt",
        "right_alt",
        "function_layer",
        "space",
        "home",
        "end",
        "page_up",
        "page_down",
    }:
        return "modifier"
    return "standard"


def _sample_spread(center: Sequence[float], samples: Sequence[Sequence[float]]) -> float:
    if not samples:
        raise AdaptiveKeyboardModelError("Contact Map key samples cannot be empty")
    try:
        distances = sorted(math.dist(center, point) for point in samples)
    except (OverflowError, TypeError, ValueError) as exc:
        raise AdaptiveKeyboardModelError("Contact Map sample geometry is invalid") from exc
    result = float(distances[len(distances) // 2])
    if not math.isfinite(result):
        raise AdaptiveKeyboardModelError("Contact Map sample spread must be finite")
    return result


def _generator_recipe() -> dict[str, object]:
    """Return every policy value capable of changing emitted GLB bytes."""

    return {
        "key_pitch_m": KEY_PITCH_M,
        "key_gap_m": KEY_GAP_M,
        "key_height_m": KEY_HEIGHT_M,
        "key_clearance_m": KEY_CLEARANCE_M,
        "keycap_top_scale": KEYCAP_TOP_SCALE,
        "minimum_keycap_size_m": MIN_KEYCAP_SIZE_M,
        "maximum_layout_extent_units": MAX_LAYOUT_EXTENT_UNITS,
        "material_policy": MATERIAL_POLICY_VERSION,
        "case_margin_m": CASE_MARGIN_M,
        "case_height_m": CASE_HEIGHT_M,
        "materials": [
            {
                "name": name,
                "color": list(color),
                "metallic": metallic,
                "roughness": roughness,
            }
            for name, color, metallic, roughness in MATERIAL_RECIPES
        ],
    }


def _model_payload(
    profile: KeyboardLayoutProfile,
    contact_map: KeyboardContactMap,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    if contact_map.layout_id != profile.layout_id:
        raise AdaptiveKeyboardModelError("Contact Map layout_id does not match the profile")
    if contact_map.layout_revision != profile.inventory_revision:
        raise AdaptiveKeyboardModelError(
            "Contact Map layout revision does not match the active key inventory"
        )

    profile_ids = [key.key_id for key in profile.keys]
    physical_ids = [key.physical_key_id for key in profile.keys]
    if profile_ids != physical_ids:
        raise AdaptiveKeyboardModelError(
            "key_id and physical_key_id must match for direct physical binding"
        )
    if len(profile_ids) != len(set(profile_ids)):
        raise AdaptiveKeyboardModelError("keyboard profile contains duplicate key IDs")

    contact_by_id = {key.key_id: key for key in contact_map.keys}
    if set(profile_ids) != set(contact_by_id):
        raise AdaptiveKeyboardModelError(
            "Contact Map keys do not match the active keyboard profile"
        )

    geometry_revision = layout_geometry_revision(profile)
    generation_recipe = _generator_recipe()
    model_revision = _canonical_hash(
        {
            "schema_version": MODEL_SCHEMA_VERSION,
            "generator_version": GENERATOR_VERSION,
            "node_contract": NODE_CONTRACT,
            "geometry_revision": geometry_revision,
            "inventory_revision": profile.inventory_revision,
            "contact_map_revision": contact_map.revision,
            "anchor_revision": contact_map.anchor_revision,
            "recipe": generation_recipe,
        }
    )

    parsed: list[dict[str, object]] = []
    min_x = math.inf
    min_z = math.inf
    max_x = -math.inf
    max_z = -math.inf
    for key in profile.keys:
        x_units = _finite_number(key.x_units, field=f"{key.key_id}.x_units")
        y_units = _finite_number(key.y_units, field=f"{key.key_id}.y_units")
        width_units = _finite_number(
            key.width_units, field=f"{key.key_id}.width_units"
        )
        height_units = _finite_number(
            key.height_units, field=f"{key.key_id}.height_units"
        )
        if (
            x_units < 0
            or y_units < 0
            or width_units <= 0
            or height_units <= 0
            or x_units + width_units > MAX_LAYOUT_EXTENT_UNITS
            or y_units + height_units > MAX_LAYOUT_EXTENT_UNITS
        ):
            raise AdaptiveKeyboardModelError(
                f"{key.key_id} has invalid layout geometry"
            )

        left = x_units * KEY_PITCH_M
        top = y_units * KEY_PITCH_M
        right = (x_units + width_units) * KEY_PITCH_M
        bottom = (y_units + height_units) * KEY_PITCH_M
        min_x, min_z = min(min_x, left), min(min_z, top)
        max_x, max_z = max(max_x, right), max(max_z, bottom)

        contact_key = contact_by_id[key.key_id]
        contact_center = [
            _finite_number(value, field=f"{key.key_id}.contact_center[{axis}]")
            for axis, value in enumerate(contact_key.center)
        ]
        contact_samples = [
            [
                _finite_number(
                    value,
                    field=f"{key.key_id}.contact_samples[{sample_index}][{axis}]",
                )
                for axis, value in enumerate(point)
            ]
            for sample_index, point in enumerate(contact_key.samples)
        ]
        contact_spread = _sample_spread(contact_center, contact_samples)
        parsed.append(
            {
                "key_id": key.key_id,
                "label": key.label,
                "browser_code": key.browser_code,
                "row": key.row,
                "layout_rect_units": [
                    x_units,
                    y_units,
                    width_units,
                    height_units,
                ],
                "layout_center_m": [
                    (x_units + width_units / 2) * KEY_PITCH_M,
                    (y_units + height_units / 2) * KEY_PITCH_M,
                ],
                "size": [
                    max(
                        MIN_KEYCAP_SIZE_M,
                        width_units * KEY_PITCH_M - KEY_GAP_M,
                    ),
                    KEY_HEIGHT_M,
                    max(
                        MIN_KEYCAP_SIZE_M,
                        height_units * KEY_PITCH_M - KEY_GAP_M,
                    ),
                ],
                "material_style": _material_style(key.key_id, key.row),
                "contact_center_reference": contact_center,
                "contact_sample_spread_reference": contact_spread,
            }
        )

    if not parsed:
        raise AdaptiveKeyboardModelError("cannot generate an empty keyboard")

    for index, first in enumerate(parsed):
        first_x, first_y, first_w, first_h = first["layout_rect_units"]
        for second in parsed[index + 1 :]:
            second_x, second_y, second_w, second_h = second["layout_rect_units"]
            overlap_x = min(first_x + first_w, second_x + second_w) - max(
                first_x, second_x
            )
            overlap_y = min(first_y + first_h, second_y + second_h) - max(
                first_y, second_y
            )
            if overlap_x > 1e-9 and overlap_y > 1e-9:
                raise AdaptiveKeyboardModelError(
                    "layout rectangles overlap: "
                    f"{first['key_id']} and {second['key_id']}"
                )

    keyboard_center_x = (min_x + max_x) / 2
    keyboard_center_z = (min_z + max_z) / 2
    key_center_y = CASE_HEIGHT_M / 2 + KEY_CLEARANCE_M + KEY_HEIGHT_M / 2
    for key in parsed:
        layout_center = key.pop("layout_center_m")
        key["center"] = [
            float(layout_center[0]) - keyboard_center_x,
            key_center_y,
            float(layout_center[1]) - keyboard_center_z,
        ]
        key["node_name"] = f"key:{key['key_id']}"

    case_size = [
        max_x - min_x + CASE_MARGIN_M * 2,
        CASE_HEIGHT_M,
        max_z - min_z + CASE_MARGIN_M * 2,
    ]
    model_payload: dict[str, object] = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "node_contract": NODE_CONTRACT,
        "generation_recipe": generation_recipe,
        "model_revision": model_revision,
        "geometry_revision": geometry_revision,
        "layout": {
            "layout_id": profile.layout_id,
            "layout_name": profile.layout_name,
            "profile_content_hash": profile.content_hash,
            "inventory_revision": profile.inventory_revision,
            "key_count": len(parsed),
        },
        "contact_binding": {
            "type": "physical_key_id",
            "coordinate_policy": "semantic_binding_not_sample_as_keycap_boundary",
            "contact_map_revision": contact_map.revision,
            "anchor_revision": contact_map.anchor_revision,
            "samples_per_key": contact_map.samples_per_key,
            "coordinate_system": contact_map.coordinate_system,
        },
        "geometry": {
            "source": "user_layout_rectangles",
            "unit": "meter",
            "layout_unit_m": KEY_PITCH_M,
            "key_gap_m": KEY_GAP_M,
            "key_height_m": KEY_HEIGHT_M,
            "key_clearance_m": KEY_CLEARANCE_M,
            "keycap_top_scale": KEYCAP_TOP_SCALE,
            "case_height_m": CASE_HEIGHT_M,
            "case_size": case_size,
            "case_center": [0.0, 0.0, 0.0],
            "bounds": {
                "min": [
                    -case_size[0] / 2,
                    -CASE_HEIGHT_M / 2,
                    -case_size[2] / 2,
                ],
                "max": [
                    case_size[0] / 2,
                    key_center_y + KEY_HEIGHT_M / 2,
                    case_size[2] / 2,
                ],
            },
        },
        "basis": {
            "long_axis_xz": [1.0, 0.0],
            "short_axis_xz": [0.0, 1.0],
            "up_axis": "+Y",
        },
        "key_count": len(parsed),
    }
    return model_payload, parsed


class _BinaryBuffer:
    def __init__(self) -> None:
        self.data = bytearray()
        self.views: list[dict[str, object]] = []
        self.accessors: list[dict[str, object]] = []

    def add_accessor(
        self,
        values: Sequence[Sequence[float | int]],
        *,
        component_type: int,
        element_type: str,
        target: int,
    ) -> int:
        if not values:
            raise AdaptiveKeyboardModelError("GLB accessor cannot be empty")
        width = len(values[0])
        if any(len(value) != width for value in values):
            raise AdaptiveKeyboardModelError("GLB accessor rows must have equal width")
        format_code = {5123: "H", 5126: "f"}.get(component_type)
        if format_code is None:
            raise AdaptiveKeyboardModelError("unsupported GLB component type")
        self.data.extend(b"\0" * (-len(self.data) % 4))
        byte_offset = len(self.data)
        flat = [item for value in values for item in value]
        try:
            self.data.extend(struct.pack("<" + format_code * len(flat), *flat))
        except (OverflowError, struct.error) as exc:
            raise AdaptiveKeyboardModelError(
                "generated GLB geometry exceeds its binary representation"
            ) from exc
        view_index = len(self.views)
        self.views.append(
            {
                "buffer": 0,
                "byteOffset": byte_offset,
                "byteLength": len(self.data) - byte_offset,
                "target": target,
            }
        )
        accessor: dict[str, object] = {
            "bufferView": view_index,
            "componentType": component_type,
            "count": len(values),
            "type": element_type,
        }
        if component_type == 5126:
            accessor["min"] = [
                float(min(row[index] for row in values)) for index in range(width)
            ]
            accessor["max"] = [
                float(max(row[index] for row in values)) for index in range(width)
            ]
        elif element_type == "SCALAR":
            accessor["min"] = [int(min(row[0] for row in values))]
            accessor["max"] = [int(max(row[0] for row in values))]
        self.accessors.append(accessor)
        return len(self.accessors) - 1


def _face_geometry(
    faces: Sequence[
        tuple[Sequence[tuple[float, float, float]], tuple[float, float, float]]
    ],
) -> tuple[
    list[tuple[float, float, float]],
    list[tuple[float, float, float]],
    list[tuple[int]],
]:
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    indices: list[tuple[int]] = []
    for corners, normal in faces:
        start = len(positions)
        positions.extend(corners)
        normals.extend([normal] * 4)
        indices.extend(
            (value,)
            for value in (start, start + 1, start + 2, start, start + 2, start + 3)
        )
    return positions, normals, indices


def _box_geometry() -> tuple[list, list, list]:
    n = 0.5
    return _face_geometry(
        (
            (((-n, n, n), (n, n, n), (n, n, -n), (-n, n, -n)), (0.0, 1.0, 0.0)),
            (((-n, -n, -n), (n, -n, -n), (n, -n, n), (-n, -n, n)), (0.0, -1.0, 0.0)),
            (((-n, -n, n), (n, -n, n), (n, n, n), (-n, n, n)), (0.0, 0.0, 1.0)),
            (((n, -n, -n), (-n, -n, -n), (-n, n, -n), (n, n, -n)), (0.0, 0.0, -1.0)),
            (((-n, -n, -n), (-n, -n, n), (-n, n, n), (-n, n, -n)), (-1.0, 0.0, 0.0)),
            (((n, -n, n), (n, -n, -n), (n, n, -n), (n, n, n)), (1.0, 0.0, 0.0)),
        )
    )


def _keycap_geometry() -> tuple[list, list, list]:
    bottom = 0.5
    top = bottom * KEYCAP_TOP_SCALE
    y0, y1 = -0.5, 0.5
    slope = bottom - top
    side_normal = 1.0 / math.sqrt(1.0 + slope * slope)
    vertical_normal = slope * side_normal
    return _face_geometry(
        (
            (((-top, y1, top), (top, y1, top), (top, y1, -top), (-top, y1, -top)), (0.0, 1.0, 0.0)),
            (((-bottom, y0, -bottom), (bottom, y0, -bottom), (bottom, y0, bottom), (-bottom, y0, bottom)), (0.0, -1.0, 0.0)),
            (((-bottom, y0, bottom), (bottom, y0, bottom), (top, y1, top), (-top, y1, top)), (0.0, vertical_normal, side_normal)),
            (((bottom, y0, -bottom), (-bottom, y0, -bottom), (-top, y1, -top), (top, y1, -top)), (0.0, vertical_normal, -side_normal)),
            (((-bottom, y0, -bottom), (-bottom, y0, bottom), (-top, y1, top), (-top, y1, -top)), (-side_normal, vertical_normal, 0.0)),
            (((bottom, y0, bottom), (bottom, y0, -bottom), (top, y1, -top), (top, y1, top)), (side_normal, vertical_normal, 0.0)),
        )
    )


def _pbr_material(
    name: str,
    color: tuple[float, float, float, float],
    *,
    metallic: float,
    roughness: float,
) -> dict[str, object]:
    return {
        "name": name,
        "pbrMetallicRoughness": {
            "baseColorFactor": list(color),
            "metallicFactor": metallic,
            "roughnessFactor": roughness,
        },
    }


def _build_glb(model: Mapping[str, object], keys: Sequence[Mapping[str, object]]) -> bytes:
    binary = _BinaryBuffer()
    box_positions, box_normals, box_indices = _box_geometry()
    key_positions, key_normals, key_indices = _keycap_geometry()
    box_position_accessor = binary.add_accessor(
        box_positions, component_type=5126, element_type="VEC3", target=34962
    )
    box_normal_accessor = binary.add_accessor(
        box_normals, component_type=5126, element_type="VEC3", target=34962
    )
    box_index_accessor = binary.add_accessor(
        box_indices, component_type=5123, element_type="SCALAR", target=34963
    )
    key_position_accessor = binary.add_accessor(
        key_positions, component_type=5126, element_type="VEC3", target=34962
    )
    key_normal_accessor = binary.add_accessor(
        key_normals, component_type=5126, element_type="VEC3", target=34962
    )
    key_index_accessor = binary.add_accessor(
        key_indices, component_type=5123, element_type="SCALAR", target=34963
    )

    materials = [
        _pbr_material(name, color, metallic=metallic, roughness=roughness)
        for name, color, metallic, roughness in MATERIAL_RECIPES
    ]
    material_indices = {
        str(material["name"]): index for index, material in enumerate(materials)
    }
    meshes: list[dict[str, object]] = [
        {
            "name": "keyboard-case-mesh",
            "primitives": [
                {
                    "attributes": {
                        "POSITION": box_position_accessor,
                        "NORMAL": box_normal_accessor,
                    },
                    "indices": box_index_accessor,
                    "material": material_indices["case"],
                }
            ],
        }
    ]
    mesh_for_style: dict[str, int] = {}
    for style in ("standard", "function", "modifier", "accent"):
        mesh_for_style[style] = len(meshes)
        meshes.append(
            {
                "name": f"keycap-{style}-mesh",
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": key_position_accessor,
                            "NORMAL": key_normal_accessor,
                        },
                        "indices": key_index_accessor,
                        "material": material_indices[style],
                    }
                ],
            }
        )

    geometry = model["geometry"]
    case_size = geometry["case_size"]
    case_center = geometry["case_center"]
    nodes: list[dict[str, object]] = [
        {
            "name": "keyboard:case",
            "mesh": 0,
            "translation": case_center,
            "scale": case_size,
            "extras": {"role": "keyboard_case"},
        }
    ]
    key_node_indices: list[int] = []
    for key in keys:
        node_index = len(nodes)
        key_node_indices.append(node_index)
        nodes.append(
            {
                "name": str(key["node_name"]),
                "mesh": mesh_for_style[str(key["material_style"])],
                "translation": key["center"],
                "scale": key["size"],
                "extras": {
                    "role": "keyboard_key",
                    "key_id": key["key_id"],
                    "label": key["label"],
                    "browser_code": key["browser_code"],
                    "row": key["row"],
                    "model_revision": model["model_revision"],
                },
            }
        )
    root_index = len(nodes)
    layout = model["layout"]
    binding = model["contact_binding"]
    nodes.append(
        {
            "name": "keyboard:root",
            "children": [0, *key_node_indices],
            "extras": {
                "role": "adaptive_keyboard_root",
                "schema_version": model["schema_version"],
                "generator_version": model["generator_version"],
                "node_contract": model["node_contract"],
                "model_revision": model["model_revision"],
                "geometry_revision": model["geometry_revision"],
                "layout_id": layout["layout_id"],
                "inventory_revision": layout["inventory_revision"],
                "contact_map_revision": binding["contact_map_revision"],
                "anchor_revision": binding["anchor_revision"],
            },
        }
    )

    document: dict[str, object] = {
        "asset": {
            "version": "2.0",
            "generator": GENERATOR_VERSION,
            "extras": {
                "schema_version": MODEL_SCHEMA_VERSION,
                "model_revision": model["model_revision"],
            },
        },
        "scene": 0,
        "scenes": [{"name": "adaptive-user-keyboard", "nodes": [root_index]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": binary.accessors,
        "bufferViews": binary.views,
        "buffers": [{"byteLength": len(binary.data)}],
    }
    try:
        json_chunk = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AdaptiveKeyboardModelError(
            "generated GLB metadata is not finite JSON"
        ) from exc
    json_chunk += b" " * (-len(json_chunk) % 4)
    binary_chunk = bytes(binary.data) + b"\0" * (-len(binary.data) % 4)
    total_length = 12 + 8 + len(json_chunk) + 8 + len(binary_chunk)
    return b"".join(
        (
            struct.pack("<4sII", b"glTF", 2, total_length),
            struct.pack("<II", len(json_chunk), 0x4E4F534A),
            json_chunk,
            struct.pack("<II", len(binary_chunk), 0x004E4942),
            binary_chunk,
        )
    )


def generate_adaptive_keyboard_model(
    profile: KeyboardLayoutProfile,
    contact_map: KeyboardContactMap,
) -> GeneratedKeyboardModel:
    """Generate deterministic GLB bytes and their revision-bound Manifest."""

    model, keys = _model_payload(profile, contact_map)
    glb = _build_glb(model, keys)
    manifest = {
        **model,
        "model": {
            "filename": GLB_FILENAME,
            "media_type": "model/gltf-binary",
            "sha256": hashlib.sha256(glb).hexdigest(),
            "byte_length": len(glb),
            "self_contained": True,
            "external_asset_used": False,
        },
        "keys": keys,
    }
    return GeneratedKeyboardModel(manifest=manifest, glb=glb)


__all__ = [
    "AdaptiveKeyboardModelError",
    "CASE_HEIGHT_M",
    "GENERATOR_VERSION",
    "GLB_FILENAME",
    "GeneratedKeyboardModel",
    "KEY_CLEARANCE_M",
    "KEY_GAP_M",
    "KEY_HEIGHT_M",
    "KEY_PITCH_M",
    "KEYCAP_TOP_SCALE",
    "MATERIAL_POLICY_VERSION",
    "MATERIAL_RECIPES",
    "MAX_LAYOUT_EXTENT_UNITS",
    "MIN_KEYCAP_SIZE_M",
    "MODEL_SCHEMA_VERSION",
    "NODE_CONTRACT",
    "generate_adaptive_keyboard_model",
    "layout_geometry_revision",
]
