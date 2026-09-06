import json
from pathlib import Path
from uuid import uuid4

import pytest

from deskvision.perception.hand_base import DetectedHand, HandLandmark
from deskvision.state.scene_state import (
    ArtifactRevisions,
    Diagnostics,
    FingertipState,
    KeyCandidateState,
    KeyHighlightState,
    KeyboardModelState,
    KeyboardPoseState,
    KeyboardState,
    SceneState,
)
from deskvision.transport.remote_inference import (
    REMOTE_INFERENCE_PROTOCOL_VERSION,
    RemoteFrameHeader,
    RemoteInferenceProtocolError,
    RemoteSceneStateEnvelope,
)


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def _header(*, sequence: int = 4, frame_id: int = 19) -> RemoteFrameHeader:
    return RemoteFrameHeader(
        session_id=str(uuid4()),
        sequence=sequence,
        source_id="windows_main",
        source_frame_id=frame_id,
        captured_at_ns=123456789,
        width=1280,
        height=720,
        byte_length=4,
    )


def _populated_state(header: RemoteFrameHeader) -> dict[str, object]:
    landmarks = tuple(HandLandmark(0.25, 0.5) for _ in range(21))
    candidate = KeyCandidateState(
        physical_key_id="key_a",
        model_node_id="key:key_a",
        label="A",
        rank=1,
        distance=0.01,
        probability=0.9,
        direct=True,
    )
    highlight = KeyHighlightState(
        physical_key_id="key_a",
        model_node_id="key:key_a",
        label="A",
        intensity=0.9,
        direct=True,
        contributors=("right:index",),
    )
    state = SceneState(
        schema_version="0.2",
        source_id=header.source_id,
        source_frame_id=header.source_frame_id,
        captured_at_ns=header.captured_at_ns,
        keyboard=KeyboardState(
            coordinate_space="aruco-anchor-reference-2d",
            pose=KeyboardPoseState(
                status="ready",
                usable=True,
                confidence=0.9,
                anchor_count=4,
                detected_marker_ids=(1, 2, 3, 4),
                reference_to_image=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
                reprojection_rmse_px=0.5,
            ),
            artifacts=ArtifactRevisions(
                layout_content="layout-content",
                layout_inventory="layout-inventory",
                anchor="anchor",
                contact_map="contact-map",
                model="model",
            ),
            model=KeyboardModelState(
                revision="model",
                sha256="a" * 64,
                uri="/api/model/keyboard.glb",
                manifest_uri="/api/model/manifest",
                key_count=82,
            ),
        ),
        hands=(DetectedHand("right", 0.9, landmarks),),
        fingertips=(
            FingertipState(
                bubble_id="right:index",
                hand_index=0,
                handedness="right",
                finger="index",
                confidence=0.9,
                image_x=0.25,
                image_y=0.5,
                reference_x=1.0,
                reference_y=2.0,
                pose_confidence=0.9,
                candidates=(candidate,),
            ),
        ),
        key_highlights=(highlight,),
        diagnostics=Diagnostics(status="ready"),
    )
    # Exercise the decoded JSON wire shape rather than Python's tuple-friendly
    # encoder input returned by SceneState.to_dict().
    return json.loads(json.dumps(state.to_dict()))


def test_frame_header_round_trip_and_binary_pairing() -> None:
    header = _header()
    decoded = RemoteFrameHeader.from_dict(header.to_dict())

    assert decoded == header
    assert header.to_dict()["protocol_version"] == REMOTE_INFERENCE_PROTOCOL_VERSION
    header.validate_jpeg(b"\xff\xd8\xff\xd9")


def test_frame_payload_is_bounded_and_exact() -> None:
    header = _header()
    with pytest.raises(RemoteInferenceProtocolError, match="length"):
        header.validate_jpeg(b"\xff\xd8\xd9")
    with pytest.raises(RemoteInferenceProtocolError, match="limit"):
        header.validate_jpeg(b"\xff\xd8\xff\xd9", max_frame_bytes=3)
    with pytest.raises(RemoteInferenceProtocolError, match="complete JPEG"):
        header.validate_jpeg(b"nope")

    with pytest.raises(RemoteInferenceProtocolError, match="protocol limit"):
        RemoteFrameHeader(
            session_id=header.session_id,
            sequence=header.sequence,
            source_id=header.source_id,
            source_frame_id=header.source_frame_id,
            captured_at_ns=header.captured_at_ns,
            width=header.width,
            height=header.height,
            byte_length=4 * 1024 * 1024 + 1,
        )


def test_scene_state_result_must_match_exact_submitted_frame() -> None:
    header = _header()
    state = SceneState.empty(
        source_id=header.source_id,
        source_frame_id=header.source_frame_id,
        captured_at_ns=header.captured_at_ns,
    )
    envelope = RemoteSceneStateEnvelope(
        session_id=header.session_id,
        sequence=header.sequence,
        source_id=header.source_id,
        source_frame_id=header.source_frame_id,
        state=state.to_dict(),
    )

    decoded = RemoteSceneStateEnvelope.from_dict(envelope.to_dict())
    decoded.validate_matches(header)
    assert decoded.state["source_frame_id"] == header.source_frame_id

    other = _header(sequence=header.sequence + 1, frame_id=header.source_frame_id)
    with pytest.raises(RemoteInferenceProtocolError, match="submitted frame"):
        decoded.validate_matches(other)


def test_populated_scene_state_is_strictly_validated() -> None:
    header = _header()
    envelope = RemoteSceneStateEnvelope(
        session_id=header.session_id,
        sequence=header.sequence,
        source_id=header.source_id,
        source_frame_id=header.source_frame_id,
        state=_populated_state(header),
    )

    RemoteSceneStateEnvelope.from_dict(envelope.to_dict()).validate_matches(header)


def test_result_rejects_cross_frame_state() -> None:
    header = _header()
    state = SceneState.empty(
        source_id=header.source_id,
        source_frame_id=header.source_frame_id + 1,
        captured_at_ns=header.captured_at_ns,
    )
    with pytest.raises(RemoteInferenceProtocolError, match="source_frame_id"):
        RemoteSceneStateEnvelope(
            session_id=header.session_id,
            sequence=header.sequence,
            source_id=header.source_id,
            source_frame_id=header.source_frame_id,
            state=state.to_dict(),
        )


def test_result_rejects_partial_or_extended_scene_state() -> None:
    header = _header()
    state = SceneState.empty(
        source_id=header.source_id,
        source_frame_id=header.source_frame_id,
        captured_at_ns=header.captured_at_ns,
    ).to_dict()
    state["unexpected"] = True

    with pytest.raises(RemoteInferenceProtocolError, match="state fields"):
        RemoteSceneStateEnvelope(
            session_id=header.session_id,
            sequence=header.sequence,
            source_id=header.source_id,
            source_frame_id=header.source_frame_id,
            state=state,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda state: state.update(hands=[{"invalid": True}]), "hands\\[0\\] fields"),
        (lambda state: state.update(diagnostics={}), "diagnostics fields"),
        (
            lambda state: state.update(
                hovered_keys=[
                    {
                        "physical_key_id": "key_a",
                        "model_node_id": "key:key_a",
                        "label": "A",
                        "intensity": 1.0,
                        "direct": True,
                        "contributors": [],
                    }
                ]
            ),
            "must equal",
        ),
    ),
)
def test_result_rejects_invalid_nested_scene_state(
    mutation,
    message: str,
) -> None:
    header = _header()
    state = SceneState.empty(
        source_id=header.source_id,
        source_frame_id=header.source_frame_id,
        captured_at_ns=header.captured_at_ns,
    ).to_dict()
    mutation(state)

    with pytest.raises(RemoteInferenceProtocolError, match=message):
        RemoteSceneStateEnvelope(
            session_id=header.session_id,
            sequence=header.sequence,
            source_id=header.source_id,
            source_frame_id=header.source_frame_id,
            state=state,
        )


def test_result_requires_the_exact_capture_timestamp() -> None:
    header = _header()
    state = SceneState.empty(
        source_id=header.source_id,
        source_frame_id=header.source_frame_id,
        captured_at_ns=header.captured_at_ns + 1,
    )
    envelope = RemoteSceneStateEnvelope(
        session_id=header.session_id,
        sequence=header.sequence,
        source_id=header.source_id,
        source_frame_id=header.source_frame_id,
        state=state.to_dict(),
    )

    with pytest.raises(RemoteInferenceProtocolError, match="submitted frame"):
        envelope.validate_matches(header)


def test_headers_reject_unknown_fields_and_noncanonical_sessions() -> None:
    payload = _header().to_dict()
    payload["token"] = "must-never-travel-in-message-json"
    with pytest.raises(RemoteInferenceProtocolError, match="extra"):
        RemoteFrameHeader.from_dict(payload)

    payload.pop("token")
    payload["session_id"] = "not-a-uuid"
    with pytest.raises(RemoteInferenceProtocolError, match="canonical UUID"):
        RemoteFrameHeader.from_dict(payload)


def test_wire_schema_reuses_the_complete_scene_state_contract() -> None:
    schema = json.loads(
        (ROOT / "contracts/remote_inference.schema.json").read_text(encoding="utf-8")
    )
    state_schema = schema["$defs"]["sceneStateEnvelope"]["properties"]["state"]

    assert state_schema["allOf"][0] == {"$ref": "scene_state.schema.json"}
    assert state_schema["allOf"][1]["properties"]["hands"]["maxItems"] == 2
