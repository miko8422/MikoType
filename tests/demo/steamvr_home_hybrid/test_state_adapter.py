from __future__ import annotations

from copy import deepcopy

import pytest

from demo.steamvr_home_hybrid.state_adapter import (
    DEFAULT_DEMO_FIXED_TRANSFORM,
    POSE_SOURCE_DEMO_FIXED,
    RevisionGate,
    SceneStateAdapter,
)


pytestmark = pytest.mark.unit

REVISIONS = RevisionGate(
    layout_content="layout-content-r1",
    layout_inventory="layout-inventory-r1",
    anchor="anchor-r1",
    contact_map="contact-map-r1",
    model="model-r1",
)
KNOWN_KEYS = ("escape", "f1")


def _highlight(
    key_id: str,
    *,
    intensity: float = 1.0,
    direct: bool = True,
) -> dict[str, object]:
    return {
        "physical_key_id": key_id,
        "model_node_id": f"key:{key_id}",
        "label": key_id.upper(),
        "intensity": intensity,
        "direct": direct,
        "contributors": ["right:index"],
    }


def _state(
    frame_id: int,
    *,
    highlights: list[dict[str, object]] | None = None,
    usable: bool = True,
) -> dict[str, object]:
    key_highlights = deepcopy(highlights or [])
    return {
        "schema_version": "0.2",
        "source_id": "fixture-camera",
        "source_frame_id": frame_id,
        "captured_at_ns": 1_000 + frame_id,
        "emitted_at_ns": 2_000 + frame_id,
        "keyboard": {
            "coordinate_space": "aruco-anchor-reference-2d",
            "pose": {
                "status": "tracked" if usable else "lost",
                "usable": usable,
                "confidence": 1.0 if usable else 0.0,
                "anchor_count": 4 if usable else 0,
                "detected_marker_ids": [0, 1, 4, 5] if usable else [],
                "reference_to_image": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
                if usable
                else None,
                "reprojection_rmse_px": 0.5 if usable else None,
                "coast_age_ms": 0.0,
            },
            "artifacts": REVISIONS.to_dict(),
            "model": {
                "revision": REVISIONS.model,
                "sha256": "a" * 64,
                "uri": "/api/model/keyboard.glb",
                "manifest_uri": "/api/model/manifest",
                "key_count": len(KNOWN_KEYS),
                "node_prefix": "key:",
            },
            "key_semantics": "likely-contact-not-mechanical-keypress",
        },
        "hands": [],
        "fingertips": [],
        "key_highlights": key_highlights,
        "hovered_keys": deepcopy(key_highlights),
        "mouse": None,
        "diagnostics": {"status": "running"},
    }


def _adapter(*, ttl_ms: float = 250.0) -> SceneStateAdapter:
    return SceneStateAdapter(
        revision_gate=REVISIONS,
        known_key_ids=KNOWN_KEYS,
        ttl_ms=ttl_ms,
    )


def test_valid_state_filters_unknown_keys_and_uses_honest_fixed_pose() -> None:
    adapter = _adapter()
    decision = adapter.ingest(
        _state(7, highlights=[_highlight("escape"), _highlight("not-in-model")]),
        session_id="session-a",
        sequence=3,
        received_at_ns=10_000,
    )

    assert decision.accepted is True
    assert decision.filtered_unknown_key_ids == ("not-in-model",)
    render_state = decision.snapshot.state
    assert render_state is not None
    assert render_state.source_frame_id == 7
    assert render_state.pose_source == POSE_SOURCE_DEMO_FIXED == "demo-fixed"
    assert render_state.keyboard_to_tracking == DEFAULT_DEMO_FIXED_TRANSFORM
    assert [item.physical_key_id for item in render_state.key_highlights] == [
        "escape"
    ]
    assert render_state.to_dict()["pose_source"] == "demo-fixed"


def test_revision_mismatch_advances_sequence_and_clears_previous_highlight() -> None:
    adapter = _adapter()
    assert adapter.ingest(
        _state(1, highlights=[_highlight("escape")]),
        session_id="session-a",
        sequence=1,
        received_at_ns=1_000,
    ).accepted

    mismatched = _state(2, highlights=[_highlight("f1")])
    mismatched["keyboard"]["artifacts"]["contact_map"] = "other"  # type: ignore[index]
    rejected = adapter.ingest(
        mismatched,
        session_id="session-a",
        sequence=2,
        received_at_ns=2_000,
    )

    assert rejected.accepted is False
    assert "contact_map revision mismatch" in rejected.reason
    assert rejected.snapshot.status == "blocked"
    assert rejected.snapshot.state is None
    assert adapter.last_sequence == 2

    older_valid = adapter.ingest(
        _state(1, highlights=[_highlight("escape")]),
        session_id="session-a",
        sequence=1,
        received_at_ns=3_000,
    )
    assert older_valid.accepted is False
    assert older_valid.snapshot.state is None


def test_latest_only_sequence_and_session_reset_rules() -> None:
    adapter = _adapter()
    adapter.ingest(
        _state(10, highlights=[_highlight("escape")]),
        session_id="session-a",
        sequence=10,
        received_at_ns=10_000,
    )
    newest = adapter.ingest(
        _state(11, highlights=[_highlight("f1")]),
        session_id="session-a",
        sequence=11,
        received_at_ns=11_000,
    )
    assert newest.snapshot.state is not None
    assert newest.snapshot.state.source_frame_id == 11

    duplicate = adapter.ingest(
        _state(99, highlights=[_highlight("escape")]),
        session_id="session-a",
        sequence=11,
        received_at_ns=12_000,
    )
    assert duplicate.accepted is False
    assert duplicate.snapshot.state is not None
    assert duplicate.snapshot.state.source_frame_id == 11

    reset = adapter.ingest(
        _state(0, highlights=[_highlight("escape")]),
        session_id="session-b",
        sequence=0,
        received_at_ns=13_000,
    )
    assert reset.accepted is True
    assert reset.snapshot.state is not None
    assert reset.snapshot.state.source_frame_id == 0
    assert adapter.active_session_id == "session-b"
    assert adapter.last_sequence == 0

    late_old_session = adapter.ingest(
        _state(12, highlights=[_highlight("f1")]),
        session_id="session-a",
        sequence=12,
        received_at_ns=14_000,
    )
    assert late_old_session.accepted is False
    assert late_old_session.reason == "retired session ignored"
    assert late_old_session.snapshot.state is not None
    assert late_old_session.snapshot.state.session_id == "session-b"


def test_ttl_expiry_clears_state_and_highlights() -> None:
    adapter = _adapter(ttl_ms=10.0)
    adapter.ingest(
        _state(1, highlights=[_highlight("escape")]),
        session_id="session-a",
        sequence=1,
        received_at_ns=1_000_000,
    )

    assert adapter.snapshot(now_ns=10_999_999).state is not None
    expired = adapter.snapshot(now_ns=11_000_000)
    assert expired.status == "stale"
    assert expired.state is None
    assert "TTL" in (expired.reason or "")


def test_valid_unusable_pose_keeps_fixed_model_but_clears_highlights() -> None:
    adapter = _adapter()
    decision = adapter.ingest(
        _state(5, highlights=[_highlight("escape")], usable=False),
        session_id="session-a",
        sequence=1,
        received_at_ns=1_000,
    )

    assert decision.accepted is True
    assert decision.snapshot.state is not None
    assert decision.snapshot.state.tracking_usable is False
    assert decision.snapshot.state.key_highlights == ()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda state: state["key_highlights"][0].__setitem__("intensity", True),
        lambda state: state["key_highlights"][0].__setitem__(
            "model_node_id", "key:f1"
        ),
        lambda state: state["hovered_keys"].clear(),
    ],
    ids=("boolean-intensity", "wrong-node", "alias-mismatch"),
)
def test_malformed_known_highlight_fails_closed(mutation) -> None:
    adapter = _adapter()
    state = _state(1, highlights=[_highlight("escape")])
    mutation(state)

    decision = adapter.ingest(
        state,
        session_id="session-a",
        sequence=1,
        received_at_ns=1_000,
    )

    assert decision.accepted is False
    assert decision.snapshot.status == "blocked"
    assert decision.snapshot.state is None


def test_scene_without_keyboard_is_an_accepted_clear_event() -> None:
    adapter = _adapter()
    adapter.ingest(
        _state(1, highlights=[_highlight("escape")]),
        session_id="session-a",
        sequence=1,
        received_at_ns=1_000,
    )
    unavailable = _state(2)
    unavailable["keyboard"] = None

    decision = adapter.ingest(
        unavailable,
        session_id="session-a",
        sequence=2,
        received_at_ns=2_000,
    )

    assert decision.accepted is True
    assert decision.snapshot.status == "unavailable"
    assert decision.snapshot.state is None
