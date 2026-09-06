"""Versioned wire contract for disabled experimental remote inference.

The module deliberately contains no socket client or FastAPI route. V0.1 uses
the same-host Windows runtime; these validators keep the future Windows camera
to remote inference boundary explicit without accidentally activating it.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
import re
from typing import Any, Mapping
from uuid import UUID

from deskvision.state.scene_state import SCENE_STATE_SCHEMA_VERSION


REMOTE_INFERENCE_PROTOCOL_VERSION = "mikotype-remote-inference-0.1"
REMOTE_INFERENCE_SUBPROTOCOL = "mikotype.remote-inference.v0.1"
REMOTE_INFERENCE_WEBSOCKET_PATH = "/ws/experimental/inference"
DEFAULT_MAX_FRAME_BYTES = 4 * 1024 * 1024
MAX_FRAME_DIMENSION = 8192

_FRAME_FIELDS = {
    "type",
    "protocol_version",
    "session_id",
    "sequence",
    "source_id",
    "source_frame_id",
    "captured_at_ns",
    "width",
    "height",
    "encoding",
    "byte_length",
}
_RESULT_FIELDS = {
    "type",
    "protocol_version",
    "session_id",
    "sequence",
    "source_id",
    "source_frame_id",
    "state",
}
_SCENE_STATE_FIELDS = {
    "schema_version",
    "source_id",
    "source_frame_id",
    "captured_at_ns",
    "emitted_at_ns",
    "keyboard",
    "hands",
    "fingertips",
    "key_highlights",
    "hovered_keys",
    "mouse",
    "diagnostics",
}
_LANDMARK_FIELDS = {"x", "y", "z", "score"}
_HAND_FIELDS = {"handedness", "score", "landmarks", "world_landmarks"}
_CANDIDATE_FIELDS = {
    "physical_key_id",
    "model_node_id",
    "label",
    "rank",
    "distance",
    "probability",
    "direct",
}
_FINGERTIP_FIELDS = {
    "bubble_id",
    "hand_index",
    "handedness",
    "finger",
    "confidence",
    "image_x",
    "image_y",
    "reference_x",
    "reference_y",
    "pose_confidence",
    "candidates",
}
_HIGHLIGHT_FIELDS = {
    "physical_key_id",
    "model_node_id",
    "label",
    "intensity",
    "direct",
    "contributors",
}
_KEYBOARD_FIELDS = {
    "coordinate_space",
    "pose",
    "artifacts",
    "model",
    "key_semantics",
}
_POSE_FIELDS = {
    "status",
    "usable",
    "confidence",
    "anchor_count",
    "detected_marker_ids",
    "reference_to_image",
    "reprojection_rmse_px",
    "coast_age_ms",
}
_ARTIFACT_FIELDS = {
    "layout_content",
    "layout_inventory",
    "anchor",
    "contact_map",
    "model",
}
_MODEL_FIELDS = {
    "revision",
    "sha256",
    "uri",
    "manifest_uri",
    "key_count",
    "node_prefix",
}
_DIAGNOSTIC_FIELDS = {
    "capture_fps",
    "frame_age_ms",
    "hand_inference_ms",
    "keyboard_inference_ms",
    "perception_ms",
    "status",
    "error",
}


class RemoteInferenceProtocolError(ValueError):
    """A remote frame/result violates the experimental wire contract."""


def _strict_mapping(
    payload: Mapping[str, Any],
    fields: set[str],
    *,
    label: str,
) -> dict[str, Any]:
    decoded = dict(payload)
    if set(decoded) != fields:
        missing = sorted(fields - set(decoded))
        extra = sorted(set(decoded) - fields)
        raise RemoteInferenceProtocolError(
            f"{label} fields do not match the protocol "
            f"(missing={missing}, extra={extra})"
        )
    return decoded


def _nonnegative_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RemoteInferenceProtocolError(f"{field} must be a non-negative integer")
    return value


def _positive_integer(value: object, *, field: str) -> int:
    parsed = _nonnegative_integer(value, field=field)
    if parsed == 0:
        raise RemoteInferenceProtocolError(f"{field} must be positive")
    return parsed


def _canonical_session_id(value: object) -> str:
    if not isinstance(value, str):
        raise RemoteInferenceProtocolError("session_id must be a canonical UUID")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise RemoteInferenceProtocolError(
            "session_id must be a canonical UUID"
        ) from exc
    if str(parsed) != value:
        raise RemoteInferenceProtocolError("session_id must be a canonical UUID")
    return value


def _source_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise RemoteInferenceProtocolError(
            "source_id must be a non-empty string of at most 128 characters"
        )
    return value


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RemoteInferenceProtocolError(f"{label} must be a JSON object")
    return dict(value)


def _array(value: object, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise RemoteInferenceProtocolError(f"{field} must be an array")
    return value


def _boolean(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise RemoteInferenceProtocolError(f"{field} must be boolean")
    return value


def _string(value: object, *, field: str, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        suffix = "a non-empty string" if nonempty else "a string"
        raise RemoteInferenceProtocolError(f"{field} must be {suffix}")
    return value


def _finite_number(value: object, *, field: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RemoteInferenceProtocolError(f"{field} must be a number")
    try:
        parsed = float(value)
    except OverflowError as exc:
        raise RemoteInferenceProtocolError(f"{field} must be finite") from exc
    if not math.isfinite(parsed):
        raise RemoteInferenceProtocolError(f"{field} must be finite")
    if minimum is not None and parsed < minimum:
        raise RemoteInferenceProtocolError(f"{field} must be at least {minimum:g}")
    return parsed


def _unit_interval(value: object, *, field: str) -> None:
    parsed = _finite_number(value, field=field)
    if not 0.0 <= parsed <= 1.0:
        raise RemoteInferenceProtocolError(f"{field} must be between 0 and 1")


def _validate_landmark(value: object, *, field: str) -> None:
    landmark = _strict_mapping(
        _mapping(value, label=field),
        _LANDMARK_FIELDS,
        label=field,
    )
    for coordinate in ("x", "y", "z"):
        _finite_number(landmark[coordinate], field=f"{field}.{coordinate}")
    _unit_interval(landmark["score"], field=f"{field}.score")


def _validate_hand(value: object, *, index: int) -> None:
    field = f"state.hands[{index}]"
    hand = _strict_mapping(_mapping(value, label=field), _HAND_FIELDS, label=field)
    if hand["handedness"] not in {"left", "right", "unknown"}:
        raise RemoteInferenceProtocolError(f"{field}.handedness is unsupported")
    _unit_interval(hand["score"], field=f"{field}.score")
    landmarks = _array(hand["landmarks"], field=f"{field}.landmarks")
    if len(landmarks) != 21:
        raise RemoteInferenceProtocolError(f"{field}.landmarks must contain 21 items")
    for landmark_index, landmark in enumerate(landmarks):
        _validate_landmark(landmark, field=f"{field}.landmarks[{landmark_index}]")
    world = _array(hand["world_landmarks"], field=f"{field}.world_landmarks")
    if len(world) not in {0, 21}:
        raise RemoteInferenceProtocolError(
            f"{field}.world_landmarks must be empty or contain 21 items"
        )
    for landmark_index, landmark in enumerate(world):
        _validate_landmark(
            landmark,
            field=f"{field}.world_landmarks[{landmark_index}]",
        )


def _validate_candidate(value: object, *, field: str) -> None:
    candidate = _strict_mapping(
        _mapping(value, label=field),
        _CANDIDATE_FIELDS,
        label=field,
    )
    _string(
        candidate["physical_key_id"],
        field=f"{field}.physical_key_id",
        nonempty=True,
    )
    node_id = _string(
        candidate["model_node_id"], field=f"{field}.model_node_id", nonempty=True
    )
    if not node_id.startswith("key:"):
        raise RemoteInferenceProtocolError(f"{field}.model_node_id must start with key:")
    _string(candidate["label"], field=f"{field}.label")
    _positive_integer(candidate["rank"], field=f"{field}.rank")
    _finite_number(candidate["distance"], field=f"{field}.distance", minimum=0)
    _unit_interval(candidate["probability"], field=f"{field}.probability")
    _boolean(candidate["direct"], field=f"{field}.direct")


def _validate_fingertip(value: object, *, index: int) -> None:
    field = f"state.fingertips[{index}]"
    fingertip = _strict_mapping(
        _mapping(value, label=field),
        _FINGERTIP_FIELDS,
        label=field,
    )
    _string(fingertip["bubble_id"], field=f"{field}.bubble_id", nonempty=True)
    _nonnegative_integer(fingertip["hand_index"], field=f"{field}.hand_index")
    if fingertip["handedness"] not in {"left", "right", "unknown"}:
        raise RemoteInferenceProtocolError(f"{field}.handedness is unsupported")
    _string(fingertip["finger"], field=f"{field}.finger", nonempty=True)
    _unit_interval(fingertip["confidence"], field=f"{field}.confidence")
    for coordinate in ("image_x", "image_y", "reference_x", "reference_y"):
        _finite_number(fingertip[coordinate], field=f"{field}.{coordinate}")
    _unit_interval(fingertip["pose_confidence"], field=f"{field}.pose_confidence")
    candidates = _array(fingertip["candidates"], field=f"{field}.candidates")
    for candidate_index, candidate in enumerate(candidates):
        _validate_candidate(candidate, field=f"{field}.candidates[{candidate_index}]")


def _validate_highlight(value: object, *, field: str) -> None:
    highlight = _strict_mapping(
        _mapping(value, label=field),
        _HIGHLIGHT_FIELDS,
        label=field,
    )
    _string(
        highlight["physical_key_id"],
        field=f"{field}.physical_key_id",
        nonempty=True,
    )
    node_id = _string(
        highlight["model_node_id"], field=f"{field}.model_node_id", nonempty=True
    )
    if not node_id.startswith("key:"):
        raise RemoteInferenceProtocolError(f"{field}.model_node_id must start with key:")
    _string(highlight["label"], field=f"{field}.label")
    _unit_interval(highlight["intensity"], field=f"{field}.intensity")
    _boolean(highlight["direct"], field=f"{field}.direct")
    contributors = _array(highlight["contributors"], field=f"{field}.contributors")
    parsed_contributors = [
        _string(item, field=f"{field}.contributors[{index}]")
        for index, item in enumerate(contributors)
    ]
    if len(set(parsed_contributors)) != len(parsed_contributors):
        raise RemoteInferenceProtocolError(f"{field}.contributors must be unique")


def _validate_keyboard(value: object) -> None:
    keyboard = _strict_mapping(
        _mapping(value, label="state.keyboard"),
        _KEYBOARD_FIELDS,
        label="state.keyboard",
    )
    if keyboard["coordinate_space"] != "aruco-anchor-reference-2d":
        raise RemoteInferenceProtocolError("state.keyboard.coordinate_space is unsupported")
    if keyboard["key_semantics"] != "likely-contact-not-mechanical-keypress":
        raise RemoteInferenceProtocolError("state.keyboard.key_semantics is unsupported")

    pose = _strict_mapping(
        _mapping(keyboard["pose"], label="state.keyboard.pose"),
        _POSE_FIELDS,
        label="state.keyboard.pose",
    )
    _string(pose["status"], field="state.keyboard.pose.status", nonempty=True)
    _boolean(pose["usable"], field="state.keyboard.pose.usable")
    _unit_interval(pose["confidence"], field="state.keyboard.pose.confidence")
    _nonnegative_integer(pose["anchor_count"], field="state.keyboard.pose.anchor_count")
    marker_ids = _array(
        pose["detected_marker_ids"], field="state.keyboard.pose.detected_marker_ids"
    )
    parsed_marker_ids = [
        _nonnegative_integer(item, field="state.keyboard.pose.detected_marker_ids[]")
        for item in marker_ids
    ]
    if any(item > 49 for item in parsed_marker_ids):
        raise RemoteInferenceProtocolError("detected marker IDs must be between 0 and 49")
    if len(set(parsed_marker_ids)) != len(parsed_marker_ids):
        raise RemoteInferenceProtocolError("detected marker IDs must be unique")
    matrix = pose["reference_to_image"]
    if matrix is not None:
        matrix_values = _array(matrix, field="state.keyboard.pose.reference_to_image")
        if len(matrix_values) != 9:
            raise RemoteInferenceProtocolError("reference_to_image must contain 9 items")
        for index, item in enumerate(matrix_values):
            _finite_number(
                item,
                field=f"state.keyboard.pose.reference_to_image[{index}]",
            )
    rmse = pose["reprojection_rmse_px"]
    if rmse is not None:
        _finite_number(
            rmse,
            field="state.keyboard.pose.reprojection_rmse_px",
            minimum=0,
        )
    _finite_number(
        pose["coast_age_ms"], field="state.keyboard.pose.coast_age_ms", minimum=0
    )

    artifacts = _strict_mapping(
        _mapping(keyboard["artifacts"], label="state.keyboard.artifacts"),
        _ARTIFACT_FIELDS,
        label="state.keyboard.artifacts",
    )
    for name, revision in artifacts.items():
        _string(revision, field=f"state.keyboard.artifacts.{name}", nonempty=True)

    model = _strict_mapping(
        _mapping(keyboard["model"], label="state.keyboard.model"),
        _MODEL_FIELDS,
        label="state.keyboard.model",
    )
    for name in ("revision", "uri", "manifest_uri"):
        _string(model[name], field=f"state.keyboard.model.{name}", nonempty=True)
    sha256 = _string(model["sha256"], field="state.keyboard.model.sha256")
    if re.fullmatch(r"[a-f0-9]{64}", sha256) is None:
        raise RemoteInferenceProtocolError("state.keyboard.model.sha256 is invalid")
    _positive_integer(model["key_count"], field="state.keyboard.model.key_count")
    if model["node_prefix"] != "key:":
        raise RemoteInferenceProtocolError("state.keyboard.model.node_prefix is unsupported")


def _validate_diagnostics(value: object) -> None:
    diagnostics = _strict_mapping(
        _mapping(value, label="state.diagnostics"),
        _DIAGNOSTIC_FIELDS,
        label="state.diagnostics",
    )
    for name in (
        "capture_fps",
        "frame_age_ms",
        "hand_inference_ms",
        "keyboard_inference_ms",
        "perception_ms",
    ):
        _finite_number(diagnostics[name], field=f"state.diagnostics.{name}", minimum=0)
    _string(diagnostics["status"], field="state.diagnostics.status", nonempty=True)
    error = diagnostics["error"]
    if error is not None:
        _string(error, field="state.diagnostics.error")


def _validate_scene_state(state: dict[str, Any]) -> None:
    if state["keyboard"] is not None:
        _validate_keyboard(state["keyboard"])
    hands = _array(state["hands"], field="state.hands")
    fingertips = _array(state["fingertips"], field="state.fingertips")
    highlights = _array(state["key_highlights"], field="state.key_highlights")
    hovered = _array(state["hovered_keys"], field="state.hovered_keys")
    limits = (
        (hands, 2, "hands"),
        (fingertips, 10, "fingertips"),
        (highlights, 256, "key_highlights"),
        (hovered, 256, "hovered_keys"),
    )
    for items, limit, field in limits:
        if len(items) > limit:
            raise RemoteInferenceProtocolError(
                f"state.{field} exceeds the protocol item limit"
            )
    for index, hand in enumerate(hands):
        _validate_hand(hand, index=index)
    for index, fingertip in enumerate(fingertips):
        _validate_fingertip(fingertip, index=index)
    for collection_name, collection in (
        ("key_highlights", highlights),
        ("hovered_keys", hovered),
    ):
        for index, highlight in enumerate(collection):
            _validate_highlight(highlight, field=f"state.{collection_name}[{index}]")
    if hovered != highlights:
        raise RemoteInferenceProtocolError(
            "state.hovered_keys must equal state.key_highlights"
        )
    if state["mouse"] is not None:
        _mapping(state["mouse"], label="state.mouse")
    _validate_diagnostics(state["diagnostics"])


@dataclass(frozen=True, slots=True)
class RemoteFrameHeader:
    """JSON header sent immediately before one binary JPEG WebSocket message."""

    session_id: str
    sequence: int
    source_id: str
    source_frame_id: int
    captured_at_ns: int
    width: int
    height: int
    byte_length: int

    def __post_init__(self) -> None:
        _canonical_session_id(self.session_id)
        _nonnegative_integer(self.sequence, field="sequence")
        _source_id(self.source_id)
        _nonnegative_integer(self.source_frame_id, field="source_frame_id")
        _nonnegative_integer(self.captured_at_ns, field="captured_at_ns")
        width = _positive_integer(self.width, field="width")
        height = _positive_integer(self.height, field="height")
        byte_length = _positive_integer(self.byte_length, field="byte_length")
        if width > MAX_FRAME_DIMENSION or height > MAX_FRAME_DIMENSION:
            raise RemoteInferenceProtocolError(
                f"frame dimensions cannot exceed {MAX_FRAME_DIMENSION} pixels"
            )
        if byte_length > DEFAULT_MAX_FRAME_BYTES:
            raise RemoteInferenceProtocolError(
                "frame byte_length exceeds the protocol limit"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "frame_header",
            "protocol_version": REMOTE_INFERENCE_PROTOCOL_VERSION,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "source_id": self.source_id,
            "source_frame_id": self.source_frame_id,
            "captured_at_ns": self.captured_at_ns,
            "width": self.width,
            "height": self.height,
            "encoding": "jpeg",
            "byte_length": self.byte_length,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RemoteFrameHeader":
        decoded = _strict_mapping(payload, _FRAME_FIELDS, label="frame header")
        if decoded["type"] != "frame_header":
            raise RemoteInferenceProtocolError("frame header type must be frame_header")
        if decoded["protocol_version"] != REMOTE_INFERENCE_PROTOCOL_VERSION:
            raise RemoteInferenceProtocolError("unsupported remote inference protocol")
        if decoded["encoding"] != "jpeg":
            raise RemoteInferenceProtocolError("experimental frame encoding must be jpeg")
        return cls(
            session_id=_canonical_session_id(decoded["session_id"]),
            sequence=_nonnegative_integer(decoded["sequence"], field="sequence"),
            source_id=_source_id(decoded["source_id"]),
            source_frame_id=_nonnegative_integer(
                decoded["source_frame_id"], field="source_frame_id"
            ),
            captured_at_ns=_nonnegative_integer(
                decoded["captured_at_ns"], field="captured_at_ns"
            ),
            width=_positive_integer(decoded["width"], field="width"),
            height=_positive_integer(decoded["height"], field="height"),
            byte_length=_positive_integer(
                decoded["byte_length"], field="byte_length"
            ),
        )

    def validate_jpeg(
        self,
        payload: bytes,
        *,
        max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
    ) -> None:
        if not isinstance(payload, bytes):
            raise RemoteInferenceProtocolError("frame payload must be binary bytes")
        if max_frame_bytes <= 0:
            raise ValueError("max_frame_bytes must be positive")
        if self.byte_length > max_frame_bytes or len(payload) > max_frame_bytes:
            raise RemoteInferenceProtocolError("frame payload exceeds the configured limit")
        if len(payload) != self.byte_length:
            raise RemoteInferenceProtocolError("frame byte length does not match its header")
        if not (payload.startswith(b"\xff\xd8") and payload.endswith(b"\xff\xd9")):
            raise RemoteInferenceProtocolError("frame payload is not a complete JPEG")


@dataclass(frozen=True, slots=True)
class RemoteSceneStateEnvelope:
    """SceneState response paired to exactly one submitted frame."""

    session_id: str
    sequence: int
    source_id: str
    source_frame_id: int
    state: Mapping[str, Any]

    def __post_init__(self) -> None:
        _canonical_session_id(self.session_id)
        _nonnegative_integer(self.sequence, field="sequence")
        _source_id(self.source_id)
        _nonnegative_integer(self.source_frame_id, field="source_frame_id")
        if not isinstance(self.state, Mapping):
            raise RemoteInferenceProtocolError("state must be a JSON object")
        copied = deepcopy(dict(self.state))
        if set(copied) != _SCENE_STATE_FIELDS:
            missing = sorted(_SCENE_STATE_FIELDS - set(copied))
            extra = sorted(set(copied) - _SCENE_STATE_FIELDS)
            raise RemoteInferenceProtocolError(
                "state fields do not match SceneState 0.2 "
                f"(missing={missing}, extra={extra})"
            )
        if copied.get("schema_version") != SCENE_STATE_SCHEMA_VERSION:
            raise RemoteInferenceProtocolError(
                f"state.schema_version must be {SCENE_STATE_SCHEMA_VERSION}"
            )
        if copied.get("source_id") != self.source_id:
            raise RemoteInferenceProtocolError("state source_id does not match envelope")
        if copied.get("source_frame_id") != self.source_frame_id:
            raise RemoteInferenceProtocolError(
                "state source_frame_id does not match envelope"
            )
        _nonnegative_integer(copied.get("captured_at_ns"), field="captured_at_ns")
        _nonnegative_integer(copied.get("emitted_at_ns"), field="emitted_at_ns")
        for field in ("hands", "fingertips", "key_highlights", "hovered_keys"):
            if not isinstance(copied.get(field), list):
                raise RemoteInferenceProtocolError(f"state.{field} must be an array")
        limits = {
            "hands": 2,
            "fingertips": 10,
            "key_highlights": 256,
            "hovered_keys": 256,
        }
        for field, limit in limits.items():
            if len(copied[field]) > limit:
                raise RemoteInferenceProtocolError(
                    f"state.{field} exceeds the protocol item limit"
                )
        if copied.get("keyboard") is not None and not isinstance(
            copied.get("keyboard"), Mapping
        ):
            raise RemoteInferenceProtocolError("state.keyboard must be an object or null")
        if copied.get("mouse") is not None and not isinstance(
            copied.get("mouse"), Mapping
        ):
            raise RemoteInferenceProtocolError("state.mouse must be an object or null")
        if not isinstance(copied.get("diagnostics"), Mapping):
            raise RemoteInferenceProtocolError("state.diagnostics must be an object")
        _validate_scene_state(copied)
        object.__setattr__(self, "state", copied)

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "scene_state",
            "protocol_version": REMOTE_INFERENCE_PROTOCOL_VERSION,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "source_id": self.source_id,
            "source_frame_id": self.source_frame_id,
            "state": deepcopy(dict(self.state)),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RemoteSceneStateEnvelope":
        decoded = _strict_mapping(payload, _RESULT_FIELDS, label="result envelope")
        if decoded["type"] != "scene_state":
            raise RemoteInferenceProtocolError("result type must be scene_state")
        if decoded["protocol_version"] != REMOTE_INFERENCE_PROTOCOL_VERSION:
            raise RemoteInferenceProtocolError("unsupported remote inference protocol")
        state = decoded["state"]
        if not isinstance(state, Mapping):
            raise RemoteInferenceProtocolError("state must be a JSON object")
        return cls(
            session_id=_canonical_session_id(decoded["session_id"]),
            sequence=_nonnegative_integer(decoded["sequence"], field="sequence"),
            source_id=_source_id(decoded["source_id"]),
            source_frame_id=_nonnegative_integer(
                decoded["source_frame_id"], field="source_frame_id"
            ),
            state=state,
        )

    def validate_matches(self, header: RemoteFrameHeader) -> None:
        if (
            self.session_id != header.session_id
            or self.sequence != header.sequence
            or self.source_id != header.source_id
            or self.source_frame_id != header.source_frame_id
            or self.state["captured_at_ns"] != header.captured_at_ns
        ):
            raise RemoteInferenceProtocolError(
                "remote result does not match the submitted frame"
            )


__all__ = [
    "DEFAULT_MAX_FRAME_BYTES",
    "MAX_FRAME_DIMENSION",
    "REMOTE_INFERENCE_PROTOCOL_VERSION",
    "REMOTE_INFERENCE_SUBPROTOCOL",
    "REMOTE_INFERENCE_WEBSOCKET_PATH",
    "RemoteFrameHeader",
    "RemoteInferenceProtocolError",
    "RemoteSceneStateEnvelope",
]
