"""Real CV coordinate-chain tests with synthetic pixels, never camera/model I/O.

Only the hand tracker is injected: its index fingertip is projected from an
existing calibrated sample. ArUco pixels, marker detection, homography, contact
ranking and 3D-node highlights use the production implementations unchanged.
These checks establish geometry/data-flow correctness, not MediaPipe accuracy
or physical-camera acceptance. Nothing is published to the live WebUI.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from deskvision.calibration.artifacts import load_keyboard_calibration_artifacts
from deskvision.core.models import FramePacket
from deskvision.keyboard.adaptive_model import generate_adaptive_keyboard_model
from deskvision.perception.aruco_keyboard import (
    ArucoKeyboardLocator,
    ArucoKeyboardLocatorConfig,
    OpenCVArucoDetector,
)
from deskvision.perception.hand_base import DetectedHand, HandLandmark, HandTrackingResult
from deskvision.perception.key_candidates import ContactKeyMapper
from deskvision.perception.pipeline import ProductionMappingPipeline
from deskvision.state.scene_state import ArtifactRevisions, KeyboardModelState


pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
WIDTH, HEIGHT = 2048, 1200
FRAME_NS = 1_000_000_000
TRANSFORMS = (
    np.asarray(((60, 0, 100), (0, 60, 100), (0, 0, 1)), dtype=float),
    np.asarray(((57, 4, 140), (-2, 55, 160), (.0015, .003, 1)), dtype=float),
)


@pytest.fixture(scope="module")
def calibrated_keyboard():
    seed = ROOT / "data/keyboards/kzzi_user_adjustable_82"
    artifacts = load_keyboard_calibration_artifacts(
        layout_path=seed / "layout.json",
        anchor_path=seed / "anchor_reference.json",
        contact_map_path=seed / "contact_map.json",
    )
    generated = generate_adaptive_keyboard_model(artifacts.layout, artifacts.contact_map)
    return artifacts, generated


def _project(points, matrix):
    return cv2.perspectiveTransform(
        np.asarray(points, dtype=np.float64).reshape(1, -1, 2), matrix
    )[0]


def _marker_image(reference, matrix):
    """Project each real dictionary marker onto its measured seed quad.

    Corner ordering is preserved, including the rotated marker stickers in
    the checked-in calibration. White margins are genuine detector input.
    """
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    image = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
    marker_corners = np.asarray(((0, 0), (127, 0), (127, 127), (0, 127)), dtype=np.float32)
    for anchor in reference.anchors:
        marker = cv2.aruco.generateImageMarker(dictionary, anchor.marker_id, 128)
        destination = _project(anchor.corners_reference, matrix).astype(np.float32)
        homography = cv2.getPerspectiveTransform(marker_corners, destination)
        projected = cv2.warpPerspective(
            marker, homography, (WIDTH, HEIGHT),
            flags=cv2.INTER_NEAREST, borderValue=255,
        )
        image = np.minimum(image, projected)
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)


class _KnownIndexTip:
    model_id = "synthetic-known-tip-no-ml-model"

    def __init__(self, image_point):
        self.image_point = image_point

    def track(self, frame):
        # All other landmarks deliberately have zero confidence: exactly one
        # index bubble participates, so a neighbor cannot become a direct hit
        # through a second synthetic finger.
        landmarks = [HandLandmark(0, 0, score=0) for _ in range(21)]
        landmarks[8] = HandLandmark(
            self.image_point[0] / frame.width,
            self.image_point[1] / frame.height,
            score=.99,
        )
        return HandTrackingResult(
            self.model_id, frame.source_id, frame.frame_id, frame.acquired_at_ns,
            0, 1_000, (DetectedHand("left", .99, tuple(landmarks)),),
        )

    def close(self):
        pass


def _pipeline(artifacts, generated, image_point):
    model = generated.manifest["model"]
    return ProductionMappingPipeline(
        hand_tracker=_KnownIndexTip(image_point),
        keyboard_locator=ArucoKeyboardLocator(
            artifacts.anchor_reference,
            detector=OpenCVArucoDetector(),
            config=ArucoKeyboardLocatorConfig(max_coast_ms=0),
            clock_ns=lambda: FRAME_NS,
        ),
        key_mapper=ContactKeyMapper(artifacts.contact_map),
        artifact_revisions=ArtifactRevisions(
            artifacts.layout.content_hash, artifacts.inventory.revision,
            artifacts.anchor_reference.revision, artifacts.contact_map.revision,
            generated.manifest["model_revision"],
        ),
        model=KeyboardModelState(
            generated.manifest["model_revision"], model["sha256"],
            "/api/model/keyboard.glb", "/api/model/manifest", len(artifacts.layout.keys),
        ),
        clock_ns=lambda: FRAME_NS,
    )


@pytest.mark.parametrize("matrix", TRANSFORMS, ids=("front", "perspective"))
@pytest.mark.parametrize("key_id", ("key_a", "key_j", "enter"))
def test_real_marker_pixels_map_calibrated_tip_to_key_and_3d_highlight(
    calibrated_keyboard, matrix, key_id,
):
    artifacts, generated = calibrated_keyboard
    reference_point = artifacts.contact_map.key(key_id).samples[0]
    image_point = _project((reference_point,), matrix)[0]
    frame = FramePacket(
        "isolated-synthetic-camera", 1, FRAME_NS, WIDTH, HEIGHT,
        _marker_image(artifacts.anchor_reference, matrix),
    )
    pipeline = _pipeline(artifacts, generated, image_point)
    try:
        state = pipeline.process(frame)
    finally:
        pipeline.close()

    assert state.diagnostics.status == "ready", state.diagnostics.error
    assert state.source_id == frame.source_id
    assert state.source_frame_id == frame.frame_id
    assert state.captured_at_ns == frame.acquired_at_ns
    assert state.keyboard.pose.usable
    assert set(state.keyboard.pose.detected_marker_ids) == set(artifacts.anchor_reference.marker_ids)
    assert len(state.fingertips) == 1
    tip = state.fingertips[0]
    assert tip.finger == "index" and tip.handedness == "right"
    assert (tip.reference_x, tip.reference_y) == pytest.approx(reference_point, abs=.08)
    assert tip.candidates[0].physical_key_id == key_id
    assert tip.candidates[0].direct
    direct = next(item for item in state.key_highlights if item.physical_key_id == key_id)
    assert direct.model_node_id == f"key:{key_id}"
    model_key = next(item for item in generated.manifest["keys"] if item["key_id"] == key_id)
    assert direct.model_node_id == model_key["node_name"]
    neighbors = [item for item in state.key_highlights if item.physical_key_id != key_id]
    assert neighbors
    assert all(0 < item.intensity < direct.intensity for item in neighbors)


def test_missing_marker_pixels_clear_mapping_even_when_tip_is_still_present(calibrated_keyboard):
    artifacts, generated = calibrated_keyboard
    matrix = TRANSFORMS[0]
    point = _project((artifacts.contact_map.key("key_a").samples[0],), matrix)[0]
    pipeline = _pipeline(artifacts, generated, point)
    frame = FramePacket(
        "isolated-synthetic-camera", 1, FRAME_NS, WIDTH, HEIGHT,
        _marker_image(artifacts.anchor_reference, matrix),
    )
    blank = FramePacket(
        frame.source_id, 2, FRAME_NS + 1_000_000, WIDTH, HEIGHT,
        np.full((HEIGHT, WIDTH, 3), 255, dtype=np.uint8),
    )
    try:
        before = pipeline.process(frame)
        after = pipeline.process(blank)
    finally:
        pipeline.close()

    assert before.key_highlights
    assert after.hands  # Hand visibility alone cannot authorize key highlights.
    assert not after.keyboard.pose.usable
    assert after.fingertips == ()
    assert after.key_highlights == ()
