"""Production anchor-reference creation and persistence checks."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from deskvision.calibration import (
    AnchorReferenceError,
    create_anchor_reference,
    load_anchor_reference,
    save_anchor_reference,
)


pytestmark = pytest.mark.unit

REGISTRATION = {
    0: ((100, 100), (120, 100), (120, 120), (100, 120)),
    1: ((300, 100), (320, 100), (320, 120), (300, 120)),
    2: ((100, 300), (120, 300), (120, 320), (100, 320)),
    3: ((300, 300), (320, 300), (320, 320), (300, 320)),
}
MARKER_KEYS = {0: "escape", 1: "delete", 2: "left_control", 3: "arrow_right"}


def test_measured_reference_round_trips_and_uses_marker_side_units(tmp_path: Path) -> None:
    reference = create_anchor_reference(REGISTRATION, MARKER_KEYS)
    path = tmp_path / "anchor.json"
    save_anchor_reference(path, reference)
    loaded = load_anchor_reference(path)

    assert loaded == reference
    assert np.asarray(reference.anchor(0).corners_reference) == pytest.approx(
        np.asarray(((0, 0), (1, 0), (1, 1), (0, 1)))
    )
    assert reference.anchor(1).corners_reference[0] == pytest.approx((10, 0))


def test_anchor_reference_rejects_tampering_and_bad_registration(tmp_path: Path) -> None:
    reference = create_anchor_reference(REGISTRATION, MARKER_KEYS)
    path = tmp_path / "anchor.json"
    save_anchor_reference(path, reference)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["anchors"][1]["corners_reference"][0][0] += 0.1
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AnchorReferenceError, match="revision"):
        load_anchor_reference(path)
    with pytest.raises(AnchorReferenceError, match="missing"):
        create_anchor_reference({0: REGISTRATION[0]}, MARKER_KEYS)
    with pytest.raises(AnchorReferenceError, match="different key"):
        create_anchor_reference(
            {0: REGISTRATION[0], 1: REGISTRATION[1]},
            {0: "escape", 1: "escape"},
        )
