"""Production multi-frame marker registration checks."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from deskvision.calibration import (
    AnchorRegistrationAccumulator,
    create_anchor_reference,
)


pytestmark = pytest.mark.unit

REGISTRATION = {
    0: ((100, 100), (120, 100), (120, 120), (100, 120)),
    1: ((300, 100), (320, 100), (320, 120), (300, 120)),
    2: ((100, 300), (120, 300), (120, 320), (100, 320)),
    3: ((300, 300), (320, 300), (320, 320), (300, 320)),
}
MARKER_KEYS = {0: "escape", 1: "delete", 2: "left_control", 3: "arrow_right"}


def _transformed(reference, matrix):
    return {
        anchor.marker_id: tuple(
            tuple(float(value) for value in point)
            for point in cv2.perspectiveTransform(
                np.asarray(anchor.corners_reference, dtype=np.float64).reshape(1, 4, 2),
                np.asarray(matrix, dtype=np.float64),
            )[0]
        )
        for anchor in reference.anchors
    }


def test_registration_accepts_four_good_markers_without_optional_pair() -> None:
    marker_keys = {**MARKER_KEYS, 4: "f6", 5: "right_alt"}
    accumulator = AnchorRegistrationAccumulator(marker_keys, required_samples=2)
    for frame_id in (1, 2):
        assert accumulator.observe(
            REGISTRATION,
            source_id="camera-a",
            frame_id=frame_id,
            timestamp_ms=float(frame_id * 10),
        )

    progress = accumulator.snapshot(timestamp_ms=20)
    rebuilt = accumulator.build_reference()

    assert progress["ready"] is True
    assert progress["selected_marker_ids"] == [0, 1, 2, 3]
    assert progress["optional_marker_ids"] == [4, 5]
    assert rebuilt.marker_ids == (0, 1, 2, 3)


def test_registration_chains_through_stable_marker_and_deduplicates() -> None:
    truth = create_anchor_reference(REGISTRATION, MARKER_KEYS)
    accumulator = AnchorRegistrationAccumulator(MARKER_KEYS, required_samples=2)
    frame_id = 0

    def pair(first: int, second: int) -> None:
        nonlocal frame_id
        for sample_index in range(2):
            frame_id += 1
            matrix = np.asarray(
                (
                    (18 + sample_index, 0.2, 80 + frame_id * 5),
                    (0.1, 19, 60 + frame_id * 3),
                    (0.0002, -0.0001, 1),
                ),
                dtype=float,
            )
            observations = _transformed(truth, matrix)
            assert accumulator.observe(
                {first: observations[first], second: observations[second]},
                source_id="camera-a",
                frame_id=frame_id,
                timestamp_ms=float(frame_id * 10),
            )

    pair(0, 1)
    pair(1, 2)
    pair(2, 3)
    progress = accumulator.snapshot()

    assert progress["ready"] is True
    assert progress["last_alignment"]["mode"] == "stable_marker_bridge"
    assert not accumulator.observe(
        {},
        source_id="camera-a",
        frame_id=frame_id,
        timestamp_ms=float(frame_id * 10),
    )
    assert accumulator.snapshot()["last_frame_reason"] == "duplicate_frame"


def test_source_epoch_change_resets_collected_registration() -> None:
    accumulator = AnchorRegistrationAccumulator(MARKER_KEYS, required_samples=2)
    partial = {0: REGISTRATION[0], 1: REGISTRATION[1]}
    assert accumulator.observe(
        partial,
        source_id="camera-a",
        frame_id=9,
        timestamp_ms=10,
    )
    assert accumulator.observe(
        partial,
        source_id="camera-b",
        frame_id=1,
        timestamp_ms=20,
    )

    progress = accumulator.snapshot()
    assert progress["accepted_frame_count"] == 1
    assert progress["last_reset_reason"] == "source_epoch_changed"


def test_many_samples_show_bounded_progress_but_unstable_reason() -> None:
    accumulator = AnchorRegistrationAccumulator(MARKER_KEYS)
    for frame_id in range(60):
        observations = dict(REGISTRATION)
        # Large alternating target motion remains an inlier distribution; this
        # reproduces the screenshot's 48/5 counter with non-ready gray rows.
        offset = 15 if frame_id % 2 else -15
        observations[1] = tuple((x + offset, y) for x, y in REGISTRATION[1])
        accumulator.observe(observations, source_id="camera", frame_id=frame_id, timestamp_ms=frame_id * 10)
    progress = accumulator.snapshot()
    marker = next(item for item in progress["markers"] if item["marker_id"] == 1)
    assert marker["sample_count"] == 48
    assert marker["completion_count"] == marker["required_samples"] == 5
    assert marker["remaining_samples"] == 0
    assert marker["stable"] is False
    assert marker["status"] == "unstable"
    assert marker["ready"] is False
    assert marker["hint"]
    assert progress["reason"] == "marker_samples_unstable"
    assert progress["ready"] is False
    with pytest.raises(ValueError, match="not ready"):
        accumulator.build_reference()


def test_snapshot_rechecks_geometry_and_does_not_latch_old_ready() -> None:
    accumulator = AnchorRegistrationAccumulator(MARKER_KEYS, required_samples=2, max_samples_per_marker=4)
    for frame_id in range(2):
        accumulator.observe(REGISTRATION, source_id="camera", frame_id=frame_id, timestamp_ms=frame_id)
    assert accumulator.ready
    for frame_id in range(2, 10):
        observations = dict(REGISTRATION)
        observations[3] = REGISTRATION[2]  # Individually stable but overlapping.
        accumulator.observe(observations, source_id="camera", frame_id=frame_id, timestamp_ms=frame_id)
    assert accumulator.snapshot()["reason"] == "overlapping_marker_centers"
    assert not accumulator.ready
    with pytest.raises(ValueError, match="not ready"):
        accumulator.build_reference()
