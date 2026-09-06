"""Production ArUco keyboard pose tracking.

The locator consumes the same :class:`~deskvision.core.models.FramePacket` as
the hand tracker.  Marker observations establish only the live transform from
the measured keyboard reference plane to the camera image; visual key layout
geometry is intentionally not involved.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import math
from threading import Lock
import time
from typing import Protocol

import cv2
import numpy as np

from deskvision.calibration.anchor_reference import AnchorReference
from deskvision.core.models import FramePacket


DICTIONARY_NAME = "DICT_4X4_50"
DICTIONARY_ID = cv2.aruco.DICT_4X4_50

Point = tuple[float, float]
Corners = tuple[Point, Point, Point, Point]
Matrix3x3 = tuple[tuple[float, float, float], ...]
ArucoObservations = Mapping[int, Sequence[Sequence[object]]]


class KeyboardPoseError(ValueError):
    """Marker observations or pose-tracker configuration are invalid."""


def _finite_positive(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) > 0.0
    )


def _corners(value: Sequence[Sequence[object]], *, field: str) -> Corners:
    if isinstance(value, (str, bytes)) or len(value) != 4:
        raise KeyboardPoseError(f"{field} must contain four ordered corners")
    points: list[Point] = []
    for index, point in enumerate(value):
        if isinstance(point, (str, bytes)) or len(point) != 2:
            raise KeyboardPoseError(f"{field}[{index}] must contain x and y")
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError) as exc:
            raise KeyboardPoseError(f"{field}[{index}] must be numeric") from exc
        if not math.isfinite(x) or not math.isfinite(y):
            raise KeyboardPoseError(f"{field}[{index}] must be finite")
        points.append((x, y))
    array = np.asarray(points, dtype=np.float32)
    if abs(float(cv2.contourArea(array))) <= 1e-8:
        raise KeyboardPoseError(f"{field} cannot be degenerate")
    return (points[0], points[1], points[2], points[3])


def _matrix_tuple(matrix: np.ndarray) -> Matrix3x3:
    normalized = np.asarray(matrix, dtype=np.float64)
    if normalized.shape != (3, 3) or not np.isfinite(normalized).all():
        raise KeyboardPoseError("transform must be a finite 3x3 matrix")
    if abs(float(normalized[2, 2])) <= 1e-12:
        raise KeyboardPoseError("transform cannot be normalized")
    normalized = normalized / normalized[2, 2]
    return tuple(tuple(float(value) for value in row) for row in normalized)


def _project(
    points: Sequence[Sequence[object]] | np.ndarray,
    matrix: Matrix3x3 | np.ndarray,
) -> np.ndarray:
    projected = cv2.perspectiveTransform(
        np.asarray(points, dtype=np.float64).reshape(1, -1, 2),
        np.asarray(matrix, dtype=np.float64),
    )[0]
    if not np.isfinite(projected).all():
        raise KeyboardPoseError("transform projected a point to infinity")
    return projected


def _marker_side_px(corners: Corners) -> float:
    array = np.asarray(corners, dtype=np.float64)
    sides = np.linalg.norm(np.roll(array, -1, axis=0) - array, axis=1)
    return float(np.median(sides))


def _plausible_marker_quad(corners: Corners, *, min_side_px: float) -> bool:
    array = np.asarray(corners, dtype=np.float32)
    sides = np.linalg.norm(np.roll(array, -1, axis=0) - array, axis=1)
    if not np.isfinite(sides).all() or float(np.min(sides)) < min_side_px:
        return False
    if float(np.max(sides)) / max(float(np.min(sides)), 1e-9) > 4.0:
        return False
    return bool(cv2.isContourConvex(array)) and abs(float(cv2.contourArea(array))) > (
        min_side_px * min_side_px * 0.2
    )


@dataclass(frozen=True, slots=True)
class ArucoDetectorConfig:
    """Tuning for small, unevenly lit ``DICT_4X4_50`` keycap markers."""

    upscale_factor: float = 1.5
    min_marker_side_px: float = 4.0
    clahe_clip_limit: float = 2.0
    clahe_grid_size: int = 8

    def __post_init__(self) -> None:
        for name, value in (
            ("upscale_factor", self.upscale_factor),
            ("min_marker_side_px", self.min_marker_side_px),
            ("clahe_clip_limit", self.clahe_clip_limit),
        ):
            if not _finite_positive(value):
                raise KeyboardPoseError(f"{name} must be positive and finite")
        if (
            isinstance(self.clahe_grid_size, bool)
            or not isinstance(self.clahe_grid_size, int)
            or self.clahe_grid_size <= 0
        ):
            raise KeyboardPoseError("clahe_grid_size must be a positive integer")


class ArucoObservationDetector(Protocol):
    def detect(self, frame: FramePacket) -> Mapping[int, Corners]: ...


class OpenCVArucoDetector:
    """Detect keycap markers and return corners in original frame pixels."""

    dictionary_name = DICTIONARY_NAME

    def __init__(self, config: ArucoDetectorConfig | None = None) -> None:
        self.config = config or ArucoDetectorConfig()
        if not hasattr(cv2, "aruco"):
            raise RuntimeError("opencv-contrib-python with cv2.aruco is required")
        parameters = cv2.aruco.DetectorParameters()
        parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        parameters.cornerRefinementWinSize = 5
        parameters.cornerRefinementMaxIterations = 40
        parameters.cornerRefinementMinAccuracy = 0.01
        parameters.adaptiveThreshWinSizeMin = 3
        parameters.adaptiveThreshWinSizeMax = 43
        parameters.adaptiveThreshWinSizeStep = 4
        parameters.minMarkerPerimeterRate = 0.015
        parameters.polygonalApproxAccuracyRate = 0.035
        parameters.minCornerDistanceRate = 0.04
        parameters.minDistanceToBorder = 2
        if hasattr(parameters, "useAruco3Detection"):
            parameters.useAruco3Detection = True
        self._detector = cv2.aruco.ArucoDetector(
            cv2.aruco.getPredefinedDictionary(DICTIONARY_ID),
            parameters,
        )
        grid = self.config.clahe_grid_size
        self._clahe = cv2.createCLAHE(
            clipLimit=self.config.clahe_clip_limit,
            tileGridSize=(grid, grid),
        )

    def detect(self, frame: FramePacket) -> dict[int, Corners]:
        image = frame.image_bgr
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8:
            raise KeyboardPoseError("ArUco input must be a uint8 NumPy BGR image")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        enhanced = self._clahe.apply(gray)
        scale = self.config.upscale_factor
        detector_input = cv2.resize(
            enhanced,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC,
        )
        corners, ids, _ = self._detector.detectMarkers(detector_input)
        if ids is None:
            return {}

        candidates: dict[int, list[np.ndarray]] = {}
        for marker_id, raw in zip(ids.flatten().tolist(), corners, strict=True):
            mapped = np.asarray(raw, dtype=np.float64).reshape(4, 2) / scale
            try:
                parsed = _corners(mapped, field=f"marker {int(marker_id)}")
            except KeyboardPoseError:
                continue
            if _plausible_marker_quad(
                parsed,
                min_side_px=self.config.min_marker_side_px,
            ):
                candidates.setdefault(int(marker_id), []).append(
                    np.asarray(parsed, dtype=np.float64)
                )

        merged: dict[int, Corners] = {}
        for marker_id, marker_candidates in candidates.items():
            if len(marker_candidates) == 1:
                aggregate = marker_candidates[0]
            else:
                groups: list[list[np.ndarray]] = []
                for candidate in marker_candidates:
                    center = np.mean(candidate, axis=0)
                    placed = False
                    for group in groups:
                        group_center = np.mean(
                            np.mean(np.stack(group), axis=0), axis=0
                        )
                        candidate_corners: Corners = tuple(
                            (float(point[0]), float(point[1])) for point in candidate
                        )  # type: ignore[assignment]
                        marker_side = max(_marker_side_px(candidate_corners), 1.0)
                        if float(np.linalg.norm(center - group_center)) <= max(
                            3.0, marker_side * 0.2
                        ):
                            group.append(candidate)
                            placed = True
                            break
                    if not placed:
                        groups.append([candidate])
                groups.sort(
                    key=lambda group: (
                        len(group),
                        abs(
                            float(
                                cv2.contourArea(
                                    np.median(np.stack(group), axis=0).astype(
                                        np.float32
                                    )
                                )
                            )
                        ),
                    ),
                    reverse=True,
                )
                if len(groups) > 1 and len(groups[0]) == len(groups[1]):
                    continue
                aggregate = np.median(np.stack(groups[0]), axis=0)
            parsed = _corners(aggregate, field=f"marker {marker_id}")
            if _plausible_marker_quad(
                parsed,
                min_side_px=self.config.min_marker_side_px,
            ):
                merged[marker_id] = parsed
        return merged


@dataclass(frozen=True, slots=True)
class KeyboardPoseEstimate:
    """Frame-correlated transform and quality for the measured keyboard plane."""

    reference_revision: str
    source_id: str
    frame_id: int
    acquired_at_ns: int
    status: str
    reference_to_image: Matrix3x3 | None
    image_to_reference: Matrix3x3 | None
    marker_ids: tuple[int, ...]
    unknown_marker_ids: tuple[int, ...]
    excluded_marker_ids: tuple[int, ...]
    confidence: float
    reprojection_rmse_px: float | None = None
    inlier_ratio: float = 0.0
    coast_age_ms: float = 0.0
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.reference_revision or not self.source_id:
            raise KeyboardPoseError("pose revisions and source_id cannot be empty")
        if self.status not in {
            "tracking",
            "tracking_degraded",
            "coasting",
            "not_found",
            "stale_frame",
        }:
            raise KeyboardPoseError(f"unsupported keyboard pose status: {self.status}")
        for name, value in (
            ("frame_id", self.frame_id),
            ("acquired_at_ns", self.acquired_at_ns),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise KeyboardPoseError(f"{name} must be a non-negative integer")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise KeyboardPoseError("pose confidence must be between 0 and 1")
        if not math.isfinite(self.inlier_ratio) or not 0 <= self.inlier_ratio <= 1:
            raise KeyboardPoseError("pose inlier_ratio must be between 0 and 1")
        if not math.isfinite(self.coast_age_ms) or self.coast_age_ms < 0:
            raise KeyboardPoseError("pose coast_age_ms must be non-negative")
        if self.reprojection_rmse_px is not None and (
            not math.isfinite(self.reprojection_rmse_px)
            or self.reprojection_rmse_px < 0
        ):
            raise KeyboardPoseError("pose RMSE must be non-negative and finite")
        has_forward = self.reference_to_image is not None
        has_inverse = self.image_to_reference is not None
        if has_forward != has_inverse:
            raise KeyboardPoseError("pose transforms must be both present or both absent")
        if self.status in {"tracking", "tracking_degraded", "coasting"} and not has_forward:
            raise KeyboardPoseError("a tracking pose must contain both transforms")
        if self.status in {"not_found", "stale_frame"} and has_forward:
            raise KeyboardPoseError("an unusable pose cannot expose stale transforms")

    @property
    def usable(self) -> bool:
        return (
            self.status in {"tracking", "tracking_degraded", "coasting"}
            and self.reference_to_image is not None
            and self.image_to_reference is not None
            and self.confidence > 0.0
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "keyboard-pose-0.1",
            "reference_revision": self.reference_revision,
            "source_id": self.source_id,
            "frame_id": self.frame_id,
            "acquired_at_ns": self.acquired_at_ns,
            "status": self.status,
            "usable": self.usable,
            "reference_to_image": (
                [list(row) for row in self.reference_to_image]
                if self.reference_to_image is not None
                else None
            ),
            "image_to_reference": (
                [list(row) for row in self.image_to_reference]
                if self.image_to_reference is not None
                else None
            ),
            "marker_ids": list(self.marker_ids),
            "unknown_marker_ids": list(self.unknown_marker_ids),
            "excluded_marker_ids": list(self.excluded_marker_ids),
            "confidence": self.confidence,
            "reprojection_rmse_px": self.reprojection_rmse_px,
            "inlier_ratio": self.inlier_ratio,
            "coast_age_ms": self.coast_age_ms,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ArucoKeyboardLocatorConfig:
    """Quality, temporal filtering, and stale-frame safety policy."""

    min_markers: int = 2
    full_tracking_markers: int = 4
    ransac_threshold_px: float = 3.0
    max_coast_ms: float = 350.0
    max_frame_age_ms: float = 500.0
    jitter_alpha: float = 0.14
    motion_alpha: float = 0.72
    jitter_threshold_px: float = 4.0
    motion_threshold_px: float = 36.0
    max_unconfirmed_jump_ratio: float = 0.9
    jump_confirmation_ratio: float = 0.12

    def __post_init__(self) -> None:
        for name, value in (
            ("min_markers", self.min_markers),
            ("full_tracking_markers", self.full_tracking_markers),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise KeyboardPoseError(f"{name} must be a positive integer")
        if self.full_tracking_markers < self.min_markers:
            raise KeyboardPoseError(
                "full_tracking_markers cannot be smaller than min_markers"
            )
        for name, value in (
            ("ransac_threshold_px", self.ransac_threshold_px),
            ("max_frame_age_ms", self.max_frame_age_ms),
            ("motion_threshold_px", self.motion_threshold_px),
            ("max_unconfirmed_jump_ratio", self.max_unconfirmed_jump_ratio),
            ("jump_confirmation_ratio", self.jump_confirmation_ratio),
        ):
            if not _finite_positive(value):
                raise KeyboardPoseError(f"{name} must be positive and finite")
        if (
            isinstance(self.max_coast_ms, bool)
            or not isinstance(self.max_coast_ms, (int, float))
            or not math.isfinite(float(self.max_coast_ms))
            or self.max_coast_ms < 0
        ):
            raise KeyboardPoseError("max_coast_ms must be non-negative and finite")
        if not 0 < self.jitter_alpha <= self.motion_alpha <= 1:
            raise KeyboardPoseError(
                "smoothing alphas must satisfy 0 < jitter <= motion <= 1"
            )
        if not 0 <= self.jitter_threshold_px < self.motion_threshold_px:
            raise KeyboardPoseError(
                "motion_threshold_px must exceed jitter_threshold_px"
            )


def _empty_pose(
    reference: AnchorReference,
    frame: FramePacket,
    *,
    status: str,
    marker_ids: Sequence[int] = (),
    unknown_marker_ids: Sequence[int] = (),
    excluded_marker_ids: Sequence[int] = (),
    reason: str | None = None,
) -> KeyboardPoseEstimate:
    return KeyboardPoseEstimate(
        reference_revision=reference.revision,
        source_id=frame.source_id,
        frame_id=frame.frame_id,
        acquired_at_ns=frame.acquired_at_ns,
        status=status,
        reference_to_image=None,
        image_to_reference=None,
        marker_ids=tuple(sorted(marker_ids)),
        unknown_marker_ids=tuple(sorted(unknown_marker_ids)),
        excluded_marker_ids=tuple(sorted(excluded_marker_ids)),
        confidence=0.0,
        reason=reason,
    )


def estimate_keyboard_pose(
    reference: AnchorReference,
    frame: FramePacket,
    observations: ArucoObservations,
    *,
    excluded_marker_ids: Sequence[int] = (),
    config: ArucoKeyboardLocatorConfig | None = None,
) -> KeyboardPoseEstimate:
    """Fit the measured reference plane to observations from ``frame``."""

    policy = config or ArucoKeyboardLocatorConfig()
    excluded = {int(marker_id) for marker_id in excluded_marker_ids}
    known = set(reference.marker_ids)
    unknown = sorted(set(int(marker_id) for marker_id in observations) - known)
    parsed: dict[int, Corners] = {}
    rejected: set[int] = set()
    for marker_id in sorted((set(observations) & known) - excluded):
        try:
            parsed[int(marker_id)] = _corners(
                observations[marker_id], field=f"marker {int(marker_id)}"
            )
        except KeyboardPoseError:
            rejected.add(int(marker_id))
    marker_ids = tuple(sorted(parsed))
    all_excluded = tuple(sorted(excluded | rejected))
    if len(marker_ids) < policy.min_markers:
        return _empty_pose(
            reference,
            frame,
            status="not_found",
            marker_ids=marker_ids,
            unknown_marker_ids=unknown,
            excluded_marker_ids=all_excluded,
            reason="insufficient_markers",
        )

    reference_points: list[Point] = []
    image_points: list[Point] = []
    for marker_id in marker_ids:
        reference_points.extend(reference.anchor(marker_id).corners_reference)
        image_points.extend(parsed[marker_id])
    source = np.asarray(reference_points, dtype=np.float64)
    target = np.asarray(image_points, dtype=np.float64)
    method = cv2.RANSAC if len(marker_ids) > 1 else 0
    matrix, mask = cv2.findHomography(
        source,
        target,
        method,
        policy.ransac_threshold_px,
    )
    if matrix is None or not np.isfinite(matrix).all():
        return _empty_pose(
            reference,
            frame,
            status="not_found",
            marker_ids=marker_ids,
            unknown_marker_ids=unknown,
            excluded_marker_ids=all_excluded,
            reason="homography_failed",
        )
    try:
        inverse = np.linalg.inv(matrix)
        projected = _project(reference_points, matrix)
    except (np.linalg.LinAlgError, KeyboardPoseError):
        return _empty_pose(
            reference,
            frame,
            status="not_found",
            marker_ids=marker_ids,
            unknown_marker_ids=unknown,
            excluded_marker_ids=all_excluded,
            reason="homography_singular",
        )
    errors = np.linalg.norm(projected - target, axis=1)
    inliers = (
        np.ones(len(errors), dtype=bool)
        if mask is None
        else mask.reshape(-1).astype(bool)
    )
    if not inliers.any():
        return _empty_pose(
            reference,
            frame,
            status="not_found",
            marker_ids=marker_ids,
            unknown_marker_ids=unknown,
            excluded_marker_ids=all_excluded,
            reason="no_homography_inliers",
        )
    rmse = float(math.sqrt(float(np.mean(np.square(errors[inliers])))))
    inlier_ratio = float(np.mean(inliers))
    coverage = min(1.0, len(marker_ids) / policy.full_tracking_markers)
    confidence = float(
        max(0.0, min(1.0, coverage * inlier_ratio * math.exp(-rmse / 5.0)))
    )
    status = (
        "tracking"
        if len(marker_ids) >= policy.full_tracking_markers
        else "tracking_degraded"
    )
    return KeyboardPoseEstimate(
        reference_revision=reference.revision,
        source_id=frame.source_id,
        frame_id=frame.frame_id,
        acquired_at_ns=frame.acquired_at_ns,
        status=status,
        reference_to_image=_matrix_tuple(matrix),
        image_to_reference=_matrix_tuple(inverse),
        marker_ids=marker_ids,
        unknown_marker_ids=tuple(unknown),
        excluded_marker_ids=all_excluded,
        confidence=confidence,
        reprojection_rmse_px=rmse,
        inlier_ratio=inlier_ratio,
    )


class ArucoKeyboardLocator:
    """Track a measured keyboard reference with smoothing and bounded coasting."""

    dictionary_name = DICTIONARY_NAME

    def __init__(
        self,
        reference: AnchorReference,
        *,
        detector: ArucoObservationDetector | None = None,
        config: ArucoKeyboardLocatorConfig | None = None,
        # FramePacket.acquired_at_ns is an epoch timestamp from the camera
        # source, so freshness must be measured against the same clock domain.
        clock_ns=time.time_ns,
    ) -> None:
        self.reference = reference
        self.detector = detector or OpenCVArucoDetector()
        self.config = config or ArucoKeyboardLocatorConfig()
        self.clock_ns = clock_ns
        self._lock = Lock()
        self._source_id: str | None = None
        self._last_frame_id: int | None = None
        self._last_acquired_at_ns: int | None = None
        self._last_good: KeyboardPoseEstimate | None = None
        self._smoothed_quad: np.ndarray | None = None
        self._pending_jump_quad: np.ndarray | None = None

    def reset(self) -> None:
        with self._lock:
            self._reset_unlocked()

    def _reset_unlocked(self) -> None:
        self._source_id = None
        self._last_frame_id = None
        self._last_acquired_at_ns = None
        self._last_good = None
        self._smoothed_quad = None
        self._pending_jump_quad = None

    def locate(
        self,
        frame: FramePacket,
        *,
        excluded_marker_ids: Sequence[int] = (),
        excluded_key_ids: Sequence[str] = (),
    ) -> KeyboardPoseEstimate:
        with self._lock:
            now_ns = self.clock_ns()
            age_ms = (now_ns - frame.acquired_at_ns) / 1_000_000.0
            if age_ms > self.config.max_frame_age_ms:
                return _empty_pose(
                    self.reference,
                    frame,
                    status="stale_frame",
                    reason="frame_age_exceeded",
                )
            if self._source_id is not None and frame.source_id != self._source_id:
                self._reset_unlocked()
            if self._source_id == frame.source_id and (
                (self._last_frame_id is not None and frame.frame_id <= self._last_frame_id)
                or (
                    self._last_acquired_at_ns is not None
                    and frame.acquired_at_ns <= self._last_acquired_at_ns
                )
            ):
                return _empty_pose(
                    self.reference,
                    frame,
                    status="stale_frame",
                    reason="duplicate_or_out_of_order_frame",
                )

            self._source_id = frame.source_id
            self._last_frame_id = frame.frame_id
            self._last_acquired_at_ns = frame.acquired_at_ns
            try:
                observations = self.detector.detect(frame)
            except (KeyboardPoseError, cv2.error) as exc:
                failed = _empty_pose(
                    self.reference,
                    frame,
                    status="not_found",
                    reason=f"marker_detection_failed: {exc}",
                )
                return self._coast_or_drop(failed)
            excluded = {int(marker_id) for marker_id in excluded_marker_ids}
            excluded.update(self.reference.marker_ids_for_keys(excluded_key_ids))
            estimate = estimate_keyboard_pose(
                self.reference,
                frame,
                observations,
                excluded_marker_ids=tuple(sorted(excluded)),
                config=self.config,
            )
            if estimate.usable:
                return self._accept_or_gate(estimate)
            return self._coast_or_drop(estimate)

    def _accept_or_gate(
        self,
        estimate: KeyboardPoseEstimate,
    ) -> KeyboardPoseEstimate:
        assert estimate.reference_to_image is not None
        raw_quad = _project(
            self.reference.reference_quad,
            estimate.reference_to_image,
        ).astype(np.float64)
        if self._smoothed_quad is None:
            self._smoothed_quad = raw_quad
            self._pending_jump_quad = None
        else:
            diagonal = max(
                float(np.linalg.norm(self._smoothed_quad[2] - self._smoothed_quad[0])),
                1.0,
            )
            displacement = float(
                np.mean(np.linalg.norm(raw_quad - self._smoothed_quad, axis=1))
            )
            if displacement / diagonal > self.config.max_unconfirmed_jump_ratio:
                consistency = math.inf
                if self._pending_jump_quad is not None:
                    consistency = float(
                        np.mean(
                            np.linalg.norm(raw_quad - self._pending_jump_quad, axis=1)
                        )
                    ) / diagonal
                if consistency > self.config.jump_confirmation_ratio:
                    self._pending_jump_quad = raw_quad
                    rejected = replace(
                        estimate,
                        status="not_found",
                        reference_to_image=None,
                        image_to_reference=None,
                        confidence=0.0,
                        reason="unconfirmed_pose_jump",
                    )
                    return self._coast_or_drop(rejected)
                self._pending_jump_quad = None
                self._smoothed_quad = raw_quad
            else:
                self._pending_jump_quad = None
                if displacement <= self.config.jitter_threshold_px:
                    alpha = self.config.jitter_alpha
                elif displacement >= self.config.motion_threshold_px:
                    alpha = self.config.motion_alpha
                else:
                    fraction = (
                        (displacement - self.config.jitter_threshold_px)
                        / (
                            self.config.motion_threshold_px
                            - self.config.jitter_threshold_px
                        )
                    )
                    alpha = self.config.jitter_alpha + fraction * (
                        self.config.motion_alpha - self.config.jitter_alpha
                    )
                self._smoothed_quad = (
                    (1.0 - alpha) * self._smoothed_quad + alpha * raw_quad
                )

        reference_quad = np.asarray(self.reference.reference_quad, dtype=np.float32)
        smoothed_matrix = cv2.getPerspectiveTransform(
            reference_quad,
            self._smoothed_quad.astype(np.float32),
        )
        try:
            inverse = np.linalg.inv(smoothed_matrix)
        except np.linalg.LinAlgError:
            failed = replace(
                estimate,
                status="not_found",
                reference_to_image=None,
                image_to_reference=None,
                confidence=0.0,
                reason="smoothed_homography_singular",
            )
            return self._coast_or_drop(failed)
        smoothed = KeyboardPoseEstimate(
            reference_revision=estimate.reference_revision,
            source_id=estimate.source_id,
            frame_id=estimate.frame_id,
            acquired_at_ns=estimate.acquired_at_ns,
            status=estimate.status,
            reference_to_image=_matrix_tuple(smoothed_matrix),
            image_to_reference=_matrix_tuple(inverse),
            marker_ids=estimate.marker_ids,
            unknown_marker_ids=estimate.unknown_marker_ids,
            excluded_marker_ids=estimate.excluded_marker_ids,
            confidence=estimate.confidence,
            reprojection_rmse_px=estimate.reprojection_rmse_px,
            inlier_ratio=estimate.inlier_ratio,
        )
        self._last_good = smoothed
        return smoothed

    def _coast_or_drop(
        self,
        estimate: KeyboardPoseEstimate,
    ) -> KeyboardPoseEstimate:
        if self._last_good is not None:
            age_ms = (
                estimate.acquired_at_ns - self._last_good.acquired_at_ns
            ) / 1_000_000.0
            if 0.0 <= age_ms <= self.config.max_coast_ms:
                decay = 1.0 - age_ms / max(self.config.max_coast_ms, 1.0)
                return KeyboardPoseEstimate(
                    reference_revision=self.reference.revision,
                    source_id=estimate.source_id,
                    frame_id=estimate.frame_id,
                    acquired_at_ns=estimate.acquired_at_ns,
                    status="coasting",
                    reference_to_image=self._last_good.reference_to_image,
                    image_to_reference=self._last_good.image_to_reference,
                    marker_ids=estimate.marker_ids,
                    unknown_marker_ids=estimate.unknown_marker_ids,
                    excluded_marker_ids=estimate.excluded_marker_ids,
                    confidence=self._last_good.confidence * decay,
                    reprojection_rmse_px=self._last_good.reprojection_rmse_px,
                    inlier_ratio=self._last_good.inlier_ratio,
                    coast_age_ms=age_ms,
                    reason=estimate.reason,
                )
        self._last_good = None
        self._smoothed_quad = None
        self._pending_jump_quad = None
        return estimate


def image_point_to_reference(
    pose: KeyboardPoseEstimate,
    point: Sequence[object],
) -> Point:
    if not pose.usable or pose.image_to_reference is None:
        raise KeyboardPoseError("pose is not usable for point mapping")
    if isinstance(point, (str, bytes)) or len(point) != 2:
        raise KeyboardPoseError("point must contain x and y")
    projected = _project((point,), pose.image_to_reference)[0]
    return (float(projected[0]), float(projected[1]))


def reference_point_to_image(
    pose: KeyboardPoseEstimate,
    point: Sequence[object],
) -> Point:
    if not pose.usable or pose.reference_to_image is None:
        raise KeyboardPoseError("pose is not usable for point mapping")
    if isinstance(point, (str, bytes)) or len(point) != 2:
        raise KeyboardPoseError("point must contain x and y")
    projected = _project((point,), pose.reference_to_image)[0]
    return (float(projected[0]), float(projected[1]))


# ``Localizer`` is the public orchestration name; ``Locator`` remains the
# concrete implementation name compatible with the original scaffold protocol.
ArucoKeyboardLocalizer = ArucoKeyboardLocator


__all__ = [
    "DICTIONARY_NAME",
    "ArucoDetectorConfig",
    "ArucoKeyboardLocator",
    "ArucoKeyboardLocalizer",
    "ArucoKeyboardLocatorConfig",
    "ArucoObservationDetector",
    "KeyboardPoseError",
    "KeyboardPoseEstimate",
    "OpenCVArucoDetector",
    "estimate_keyboard_pose",
    "image_point_to_reference",
    "reference_point_to_image",
]
