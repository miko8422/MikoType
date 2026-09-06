"""Minimal SceneState contract validation without third-party dependencies."""

import json
from pathlib import Path

import pytest

from deskvision.state.scene_state import SCENE_STATE_SCHEMA_VERSION, SceneState


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.unit


def test_empty_state_matches_required_top_level_shape() -> None:
    state = SceneState(
        schema_version=SCENE_STATE_SCHEMA_VERSION,
        source_id="windows_main",
        source_frame_id=0,
        captured_at_ns=0,
    ).to_dict()
    schema = json.loads((ROOT / "contracts/scene_state.schema.json").read_text())

    assert set(state) == set(schema["required"])
    assert state["schema_version"] == SCENE_STATE_SCHEMA_VERSION
    assert state["keyboard"] is None
    assert state["hands"] == []
    assert state["fingertips"] == []
    assert state["key_highlights"] == []
    assert state["hovered_keys"] == []
    assert state["mouse"] is None
    assert "image_bgr" not in state
