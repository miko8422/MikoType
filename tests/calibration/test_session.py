"""Production reference-space contact-calibration session checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from deskvision.calibration import (
    ContactCalibrationSession,
    RevisionMismatchError,
    build_layout_inventory,
    create_anchor_reference,
    load_contact_map,
)


pytestmark = pytest.mark.unit


def _profile(*key_ids: str) -> dict[str, object]:
    return {
        "layout_id": "board",
        "calibration_order": list(key_ids),
        "keys": [
            {
                "key_id": key_id,
                "label": key_id,
                "browser_code": key_id,
                "model_key_id": key_id,
            }
            for key_id in key_ids
        ],
    }


def _reference(*, offset: float = 0.0):
    return create_anchor_reference(
        {
            0: ((0, 0), (10, 0), (10, 10), (0, 10)),
            1: (
                (offset + 100, 0),
                (offset + 110, 0),
                (offset + 110, 10),
                (offset + 100, 10),
            ),
        },
        {0: "key_a", 1: "space"},
    )


def test_session_persists_five_samples_per_key_and_finalizes(tmp_path: Path) -> None:
    inventory = build_layout_inventory(_profile("key_a", "space"))
    reference = _reference()
    draft_path = tmp_path / "contact.draft.json"
    final_path = tmp_path / "contact.json"
    session = ContactCalibrationSession.resume_or_new(
        inventory,
        reference,
        draft_path=draft_path,
        final_path=final_path,
    )

    for point in ((1, 2), (1.2, 2), (0.8, 2.1), (1.1, 1.9), (1, 2)):
        assert session.capture_reference_point(point).accepted
    assert session.current_key is not None
    assert session.current_key.key_id == "space"
    for point in ((3, 3), (5, 3), (7, 3), (9, 3), (11, 3)):
        result = session.capture_reference_point(point)

    assert result.complete
    assert draft_path.exists() and final_path.exists()
    final = load_contact_map(
        final_path,
        inventory=inventory,
        anchor_revision=reference.revision,
    )
    assert final.key("space").center == pytest.approx((7, 3))

    resumed = ContactCalibrationSession.resume_or_new(
        inventory,
        reference,
        draft_path=draft_path,
        final_path=final_path,
    )
    assert resumed.complete and resumed.final_map == final


def test_session_controls_and_revision_gate(tmp_path: Path) -> None:
    inventory = build_layout_inventory(_profile("key_a", "space"))
    reference = _reference()
    draft_path = tmp_path / "contact.draft.json"
    session = ContactCalibrationSession.resume_or_new(
        inventory,
        reference,
        draft_path=draft_path,
    )
    assert session.capture_reference_point((1, 1)).accepted
    assert session.capture_reference_point((2, 2)).accepted
    session.pause()
    assert session.capture_reference_point((3, 3)).reason == "paused"
    session.resume()
    assert session.undo_last().reason == "undone"
    assert session.restart_current_key().reason == "current_key_restarted"
    assert session.progress()["captured_samples"] == 0

    with pytest.raises(RevisionMismatchError, match="anchor"):
        ContactCalibrationSession.resume_or_new(
            inventory,
            _reference(offset=5),
            draft_path=draft_path,
        )


def test_failed_durable_write_rolls_back_in_memory(monkeypatch, tmp_path: Path) -> None:
    inventory = build_layout_inventory(_profile("key_a", "space"))
    session = ContactCalibrationSession.resume_or_new(
        inventory,
        _reference(),
        draft_path=tmp_path / "contact.draft.json",
    )
    assert session.capture_reference_point((1, 1)).accepted

    def fail_write(*_args, **_kwargs):
        raise OSError("simulated durable-write failure")

    monkeypatch.setattr("deskvision.calibration.session.save_calibration_draft", fail_write)
    with pytest.raises(OSError, match="durable-write"):
        session.capture_reference_point((2, 2))
    assert session.progress()["captured_samples"] == 1
