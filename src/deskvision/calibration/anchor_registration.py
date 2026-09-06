"""Robust multi-frame registration of sparse keyboard marker anchors."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from itertools import combinations
import math

import cv2
import numpy as np

from .anchor_reference import (
    AnchorReference,
    AnchorReferenceError,
    Corners,
    create_anchor_reference,
)


ANCHOR_REGISTRATION_SCHEMA_VERSION = "keyboard-anchor-registration-progress-0.1"
DEFAULT_SAMPLES_PER_MARKER = 5
DEFAULT_MAX_SPREAD_REFERENCE = 0.12
DEFAULT_MINIMUM_MARKERS = 4


def _corners(value: object, *, field: str) -> Corners:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise AnchorReferenceError(f"{field} must contain four ordered corners")
    points: list[tuple[float, float]] = []
    for index, point in enumerate(value):
        if (
            not isinstance(point, Sequence)
            or isinstance(point, (str, bytes))
            or len(point) != 2
        ):
            raise AnchorReferenceError(f"{field}[{index}] must contain x and y")
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError) as exc:
            raise AnchorReferenceError(f"{field}[{index}] must be numeric") from exc
        if not math.isfinite(x) or not math.isfinite(y):
            raise AnchorReferenceError(f"{field}[{index}] must be finite")
        points.append((x, y))
    array = np.asarray(points, dtype=np.float32)
    if not cv2.isContourConvex(array) or abs(float(cv2.contourArea(array))) <= 1e-8:
        raise AnchorReferenceError(f"{field} cannot be degenerate or non-convex")
    return (points[0], points[1], points[2], points[3])


def _project(points: object, matrix: np.ndarray) -> np.ndarray:
    projected = cv2.perspectiveTransform(
        np.asarray(points, dtype=np.float64).reshape(1, -1, 2),
        np.asarray(matrix, dtype=np.float64),
    )[0]
    if not np.isfinite(projected).all():
        raise AnchorReferenceError("registration transform projected a point to infinity")
    return projected


def _marker_side(corners: Corners) -> float:
    array = np.asarray(corners, dtype=np.float64)
    sides = np.linalg.norm(np.roll(array, -1, axis=0) - array, axis=1)
    return float(np.median(sides))


def _plausible(corners: Corners, *, min_side: float) -> bool:
    array = np.asarray(corners, dtype=np.float32)
    sides = np.linalg.norm(np.roll(array, -1, axis=0) - array, axis=1)
    if not np.isfinite(sides).all() or float(np.min(sides)) < min_side:
        return False
    if float(np.max(sides)) / max(float(np.min(sides)), 1e-9) > 4.0:
        return False
    return bool(cv2.isContourConvex(array)) and abs(float(cv2.contourArea(array))) > (
        min_side * min_side * 0.2
    )


class AnchorRegistrationAccumulator:
    """Accumulate partial frames into one robust marker-board reference.

    The reference marker seeds the coordinate system. Once another marker is
    stable it may bridge later observations, so registration does not require
    every marker to remain visible in the same frame.
    """

    def __init__(
        self,
        marker_key_ids: Mapping[int, str],
        *,
        reference_marker_id: int = 0,
        required_samples: int = DEFAULT_SAMPLES_PER_MARKER,
        max_spread_reference: float = DEFAULT_MAX_SPREAD_REFERENCE,
        minimum_markers: int | None = None,
        max_samples_per_marker: int = 48,
        seen_frame_capacity: int = 512,
    ) -> None:
        if len(marker_key_ids) < 2:
            raise AnchorReferenceError("registration requires at least two markers")
        if reference_marker_id not in marker_key_ids:
            raise AnchorReferenceError("reference marker must have a key assignment")
        if required_samples < 2:
            raise AnchorReferenceError("required_samples must be at least two")
        if minimum_markers is None:
            minimum_markers = min(DEFAULT_MINIMUM_MARKERS, len(marker_key_ids))
        if minimum_markers < 2 or minimum_markers > len(marker_key_ids):
            raise AnchorReferenceError(
                "minimum_markers must fit within the assigned marker count"
            )
        if not math.isfinite(max_spread_reference) or max_spread_reference <= 0:
            raise AnchorReferenceError("max_spread_reference must be positive")
        if max_samples_per_marker < required_samples:
            raise AnchorReferenceError(
                "max_samples_per_marker cannot be smaller than required_samples"
            )
        if seen_frame_capacity < 1:
            raise AnchorReferenceError("seen_frame_capacity must be positive")
        normalized = {int(marker_id): str(key_id) for marker_id, key_id in marker_key_ids.items()}
        if any(marker_id < 0 for marker_id in normalized):
            raise AnchorReferenceError("marker IDs must be non-negative")
        if any(not key_id for key_id in normalized.values()):
            raise AnchorReferenceError("marker key IDs cannot be empty")
        if len(set(normalized.values())) != len(normalized):
            raise AnchorReferenceError("markers must be assigned to different keys")

        self.marker_key_ids = normalized
        self.reference_marker_id = int(reference_marker_id)
        self.required_samples = int(required_samples)
        self.minimum_markers = int(minimum_markers)
        self.max_spread_reference = float(max_spread_reference)
        self.max_samples_per_marker = int(max_samples_per_marker)
        self.seen_frame_capacity = int(seen_frame_capacity)
        self._samples: dict[int, list[np.ndarray]] = {
            marker_id: [] for marker_id in normalized
        }
        self._last_seen_ms: dict[int, float] = {}
        self._seen_frames: set[tuple[str, int]] = set()
        self._seen_frame_order: deque[tuple[str, int]] = deque()
        self._source_id: str | None = None
        self._last_frame_id: int | None = None
        self._accepted_frame_count = 0
        self._last_frame_reason = "empty"
        self._last_reset_reason: str | None = None
        self._last_contribution: dict[str, object] | None = None
        self._last_alignment: dict[str, object] | None = None

    def reset(self, *, reason: str = "explicit_reset") -> None:
        self._samples = {marker_id: [] for marker_id in self.marker_key_ids}
        self._last_seen_ms.clear()
        self._seen_frames.clear()
        self._seen_frame_order.clear()
        self._source_id = None
        self._last_frame_id = None
        self._accepted_frame_count = 0
        self._last_frame_reason = "reset"
        self._last_reset_reason = reason
        self._last_contribution = None
        self._last_alignment = None

    def observe(
        self,
        observations: Mapping[int, Sequence[Sequence[object]]],
        *,
        source_id: str,
        frame_id: int,
        timestamp_ms: float,
    ) -> bool:
        if not source_id:
            raise AnchorReferenceError("registration source_id cannot be empty")
        if isinstance(frame_id, bool) or not isinstance(frame_id, int):
            raise AnchorReferenceError("registration frame_id must be an integer")
        if not math.isfinite(timestamp_ms):
            raise AnchorReferenceError("registration timestamp must be finite")
        if self._source_id is not None and (
            source_id != self._source_id
            or (self._last_frame_id is not None and frame_id < self._last_frame_id)
        ):
            self.reset(reason="source_epoch_changed")
        self._source_id = source_id
        self._last_frame_id = frame_id

        identity = (source_id, frame_id)
        if identity in self._seen_frames:
            self._last_frame_reason = "duplicate_frame"
            return False
        self._seen_frames.add(identity)
        self._seen_frame_order.append(identity)
        while len(self._seen_frame_order) > self.seen_frame_capacity:
            self._seen_frames.discard(self._seen_frame_order.popleft())

        parsed: dict[int, Corners] = {}
        for marker_id in set(observations) & set(self.marker_key_ids):
            try:
                candidate = _corners(observations[marker_id], field=f"marker {marker_id}")
            except AnchorReferenceError:
                continue
            if _plausible(candidate, min_side=3.0):
                parsed[int(marker_id)] = candidate
                self._last_seen_ms[int(marker_id)] = float(timestamp_ms)

        alignment = self._alignment_for_frame(parsed)
        if alignment is None:
            if self._last_frame_reason not in {
                "bridge_alignment_failed",
                "bridge_alignment_quality_rejected",
            }:
                self._last_frame_reason = (
                    "stable_bridge_not_visible"
                    if self._samples[self.reference_marker_id]
                    else "reference_marker_required_to_seed"
                )
            return False
        image_to_reference, bridge_ids, details = alignment
        independent_ids = sorted(set(parsed) - set(bridge_ids))
        if not independent_ids:
            self._last_frame_reason = "bridge_visible_without_independent_target"
            return False

        prepared: dict[int, np.ndarray] = {}
        for marker_id in independent_ids:
            projected = _project(parsed[marker_id], image_to_reference)
            candidate: Corners = tuple(
                (float(x), float(y)) for x, y in projected
            )  # type: ignore[assignment]
            if _plausible(candidate, min_side=0.1):
                prepared[marker_id] = np.asarray(candidate, dtype=np.float64)
        if self.reference_marker_id in bridge_ids:
            prepared[self.reference_marker_id] = np.asarray(
                ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),
                dtype=np.float64,
            )
        if not prepared:
            self._last_frame_reason = "independent_target_projection_rejected"
            return False

        for marker_id, sample in prepared.items():
            self._samples[marker_id].append(sample)
            if len(self._samples[marker_id]) > self.max_samples_per_marker:
                del self._samples[marker_id][:-self.max_samples_per_marker]
        self._accepted_frame_count += 1
        self._last_frame_reason = "contributed"
        self._last_alignment = details
        self._last_contribution = {
            "source_id": source_id,
            "frame_id": frame_id,
            "timestamp_ms": float(timestamp_ms),
            "alignment_mode": details["mode"],
            "bridge_marker_ids": list(bridge_ids),
            "contributed_marker_ids": sorted(prepared),
        }
        return True

    def _alignment_for_frame(
        self,
        parsed: Mapping[int, Corners],
    ) -> tuple[np.ndarray, tuple[int, ...], dict[str, object]] | None:
        reference = parsed.get(self.reference_marker_id)
        canonical = np.asarray(
            ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),
            dtype=np.float32,
        )
        if reference is not None:
            matrix = cv2.getPerspectiveTransform(
                np.asarray(reference, dtype=np.float32), canonical
            )
            if np.isfinite(matrix).all():
                return matrix, (self.reference_marker_id,), {
                    "mode": "reference_marker",
                    "bridge_marker_ids": [self.reference_marker_id],
                    "reprojection_rmse_reference": 0.0,
                    "inlier_ratio": 1.0,
                }
            return None

        estimates = self._estimates()
        bridge_ids = tuple(
            marker_id
            for marker_id in sorted(set(parsed) & set(estimates))
            if int(estimates[marker_id]["inlier_count"]) >= self.required_samples
            and isinstance(estimates[marker_id]["spread"], float)
            and float(estimates[marker_id]["spread"]) <= self.max_spread_reference
        )
        if not bridge_ids:
            return None
        image_points = np.concatenate(
            [np.asarray(parsed[marker_id], dtype=np.float64) for marker_id in bridge_ids]
        )
        reference_points = np.concatenate(
            [np.asarray(estimates[marker_id]["aggregate"], dtype=np.float64) for marker_id in bridge_ids]
        )
        if len(bridge_ids) == 1:
            matrix = cv2.getPerspectiveTransform(
                image_points.astype(np.float32), reference_points.astype(np.float32)
            )
            inliers = np.ones(len(image_points), dtype=bool)
        else:
            matrix, mask = cv2.findHomography(
                image_points,
                reference_points,
                cv2.RANSAC,
                max(0.05, self.max_spread_reference),
            )
            if matrix is None:
                self._last_frame_reason = "bridge_alignment_failed"
                return None
            inliers = np.ones(len(image_points), dtype=bool) if mask is None else mask.reshape(-1).astype(bool)
        if not np.isfinite(matrix).all() or not bool(np.any(inliers)):
            self._last_frame_reason = "bridge_alignment_failed"
            return None
        errors = np.linalg.norm(_project(image_points, matrix) - reference_points, axis=1)
        ratio = float(np.mean(inliers))
        rmse = float(math.sqrt(float(np.mean(np.square(errors[inliers])))))
        if ratio < 0.6 or rmse > max(0.08, self.max_spread_reference * 1.5):
            self._last_frame_reason = "bridge_alignment_quality_rejected"
            return None
        return matrix, bridge_ids, {
            "mode": "stable_marker_bridge",
            "bridge_marker_ids": list(bridge_ids),
            "reprojection_rmse_reference": rmse,
            "inlier_ratio": ratio,
        }

    @staticmethod
    def _robust(samples: Sequence[np.ndarray]) -> dict[str, object]:
        if not samples:
            return {
                "aggregate": None,
                "observation_count": 0,
                "inlier_count": 0,
                "outlier_count": 0,
                "spread": None,
            }
        stack = np.stack(samples).astype(np.float64)
        initial = np.median(stack, axis=0)
        residual = np.sqrt(np.mean(np.square(stack - initial), axis=(1, 2)))
        center = float(np.median(residual))
        mad = float(np.median(np.abs(residual - center)))
        cutoff = max(0.025, center + 3.0 * 1.4826 * mad)
        inliers = residual <= cutoff
        if not bool(np.any(inliers)):
            inliers[int(np.argmin(residual))] = True
        aggregate = np.median(stack[inliers], axis=0)
        spread = float(math.sqrt(float(np.mean(np.square(stack[inliers] - aggregate)))))
        return {
            "aggregate": aggregate,
            "observation_count": len(stack),
            "inlier_count": int(np.count_nonzero(inliers)),
            "outlier_count": int(len(stack) - np.count_nonzero(inliers)),
            "spread": spread,
        }

    def _estimates(self) -> dict[int, dict[str, object]]:
        return {
            marker_id: self._robust(samples)
            for marker_id, samples in self._samples.items()
        }

    @staticmethod
    def _geometry_quality(estimates: Mapping[int, Mapping[str, object]]) -> tuple[bool, str]:
        aggregates: list[np.ndarray] = []
        for estimate in estimates.values():
            aggregate = estimate.get("aggregate")
            if not isinstance(aggregate, np.ndarray):
                return False, "incomplete_marker_set"
            contour = aggregate.astype(np.float32)
            sides = np.linalg.norm(np.roll(aggregate, -1, axis=0) - aggregate, axis=1)
            if (
                not cv2.isContourConvex(contour)
                or float(cv2.contourArea(contour, oriented=True)) <= 0.08
                or float(np.min(sides)) <= 0.1
                or float(np.max(sides)) / max(float(np.min(sides)), 1e-9) > 4.0
            ):
                return False, "implausible_marker_quad"
            aggregates.append(aggregate)
        centers = [np.mean(aggregate, axis=0) for aggregate in aggregates]
        distances = [
            float(np.linalg.norm(left - right))
            for index, left in enumerate(centers)
            for right in centers[index + 1 :]
        ]
        if distances and min(distances) < 0.25:
            return False, "overlapping_marker_centers"
        if len(centers) >= 4:
            hull = cv2.convexHull(np.asarray(centers, dtype=np.float32))
            if abs(float(cv2.contourArea(hull))) < 0.5:
                return False, "anchor_distribution_too_narrow"
        return True, "geometry_good"

    def snapshot(self, *, timestamp_ms: float | None = None) -> dict[str, object]:
        estimates = self._estimates()
        markers: list[dict[str, object]] = []
        ready_ids: list[int] = []
        for marker_id in sorted(self.marker_key_ids):
            estimate = estimates[marker_id]
            spread = estimate["spread"]
            stable = isinstance(spread, float) and spread <= self.max_spread_reference
            ready = int(estimate["inlier_count"]) >= self.required_samples and stable
            if ready:
                ready_ids.append(marker_id)
            last_seen = self._last_seen_ms.get(marker_id)
            markers.append(
                {
                    "marker_id": marker_id,
                    "key_id": self.marker_key_ids[marker_id],
                    "sample_count": int(estimate["inlier_count"]),
                    "observation_count": int(estimate["observation_count"]),
                    "required_samples": self.required_samples,
                    "ready": ready,
                    "spread_reference": spread,
                    "last_seen_age_ms": (
                        max(0.0, timestamp_ms - last_seen)
                        if timestamp_ms is not None and last_seen is not None
                        else None
                    ),
                }
            )

        selected: tuple[int, ...] = ()
        reason = "collecting_marker_samples"
        if len(ready_ids) >= self.minimum_markers and self.reference_marker_id in ready_ids:
            for size in range(len(ready_ids), self.minimum_markers - 1, -1):
                for candidate in combinations(ready_ids, size):
                    if self.reference_marker_id not in candidate:
                        continue
                    valid, candidate_reason = self._geometry_quality(
                        {marker_id: estimates[marker_id] for marker_id in candidate}
                    )
                    reason = candidate_reason
                    if valid:
                        selected = candidate
                        reason = "ready"
                        break
                if selected:
                    break
        elif len(ready_ids) >= self.minimum_markers:
            reason = "reference_marker_not_ready"
        ready = bool(selected)
        return {
            "schema_version": ANCHOR_REGISTRATION_SCHEMA_VERSION,
            "ready": ready,
            "reason": reason,
            "required_samples": self.required_samples,
            "reference_marker_id": self.reference_marker_id,
            "accepted_frame_count": self._accepted_frame_count,
            "collected_marker_count": len(ready_ids),
            "required_marker_count": self.minimum_markers,
            "total_marker_count": len(self.marker_key_ids),
            "ready_marker_ids": ready_ids,
            "selected_marker_ids": list(selected),
            "optional_marker_ids": sorted(set(self.marker_key_ids) - set(selected)),
            "markers": markers,
            "last_contribution": self._last_contribution,
            "last_alignment": self._last_alignment,
            "last_frame_reason": self._last_frame_reason,
            "last_reset_reason": self._last_reset_reason,
        }

    @property
    def ready(self) -> bool:
        return bool(self.snapshot()["ready"])

    def build_reference(self) -> AnchorReference:
        progress = self.snapshot()
        if not progress["ready"]:
            raise AnchorReferenceError(
                f"marker registration is not ready: {progress['reason']}"
            )
        estimates = self._estimates()
        selected = tuple(int(value) for value in progress["selected_marker_ids"])
        observations: dict[int, Corners] = {}
        for marker_id in selected:
            aggregate = estimates[marker_id]["aggregate"]
            assert isinstance(aggregate, np.ndarray)
            observations[marker_id] = tuple(
                (round(float(x), 9), round(float(y), 9)) for x, y in aggregate
            )  # type: ignore[assignment]
        return create_anchor_reference(
            observations,
            {marker_id: self.marker_key_ids[marker_id] for marker_id in selected},
            reference_marker_id=self.reference_marker_id,
        )


__all__ = [
    "ANCHOR_REGISTRATION_SCHEMA_VERSION",
    "DEFAULT_MAX_SPREAD_REFERENCE",
    "DEFAULT_MINIMUM_MARKERS",
    "DEFAULT_SAMPLES_PER_MARKER",
    "AnchorRegistrationAccumulator",
]
