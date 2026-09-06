"""Fail-closed SceneState adapter for the SteamVR Home hybrid Demo.

Production ``SceneState 0.2`` currently contains a calibrated keyboard-plane
Homography, not a metric SteamVR pose.  This adapter therefore emits an
explicitly labelled ``demo-fixed`` pose for offline replay and compositor
testing.  It must not be mistaken for real keyboard-to-SteamVR calibration.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import math
from threading import RLock
import time
from typing import Any


SCENE_STATE_SCHEMA_VERSION = "0.2"
ADAPTED_STATE_SCHEMA_VERSION = "steamvr-home-hybrid-state-0.1"
POSE_SOURCE_DEMO_FIXED = "demo-fixed"
KEY_CANDIDATE_SEMANTICS = "likely-contact-not-mechanical-keypress"

REVISION_FIELDS = (
    "layout_content",
    "layout_inventory",
    "anchor",
    "contact_map",
    "model",
)

# Row-major OpenVR-style 3x4 test transform.  The exported render-model basis
# is X-right, Y-forward, Z-up; this places it 0.75 m high and 0.75 m in front
# of the tracking origin while preserving handedness.  It is only a mock pose.
DEFAULT_DEMO_FIXED_TRANSFORM = (
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.75,
    0.0,
    -1.0,
    0.0,
    -0.75,
)

_TOP_LEVEL_FIELDS = {
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


class StateContractError(ValueError):
    """An untrusted state cannot safely drive the VR mock."""


@dataclass(frozen=True, slots=True)
class RevisionGate:
    """Exact artifact revisions required by the currently loaded model."""

    layout_content: str
    layout_inventory: str
    anchor: str
    contact_map: str
    model: str

    def __post_init__(self) -> None:
        for field in REVISION_FIELDS:
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"revision {field} must be a non-empty string")

    def to_dict(self) -> dict[str, str]:
        return {field: getattr(self, field) for field in REVISION_FIELDS}


@dataclass(frozen=True, slots=True)
class AdaptedHighlight:
    physical_key_id: str
    model_node_id: str
    label: str
    intensity: float
    direct: bool
    contributors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "physical_key_id": self.physical_key_id,
            "model_node_id": self.model_node_id,
            "label": self.label,
            "intensity": self.intensity,
            "direct": self.direct,
            "contributors": list(self.contributors),
        }


@dataclass(frozen=True, slots=True)
class AdaptedSceneState:
    """Small renderer-facing state with an honestly labelled fixed pose."""

    session_id: str
    sequence: int
    source_id: str
    source_frame_id: int
    captured_at_ns: int
    emitted_at_ns: int
    received_at_ns: int
    pose_source: str
    keyboard_to_tracking: tuple[float, ...]
    tracking_usable: bool
    key_highlights: tuple[AdaptedHighlight, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": ADAPTED_STATE_SCHEMA_VERSION,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "source_id": self.source_id,
            "source_frame_id": self.source_frame_id,
            "captured_at_ns": self.captured_at_ns,
            "emitted_at_ns": self.emitted_at_ns,
            "received_at_ns": self.received_at_ns,
            "pose_source": self.pose_source,
            "keyboard_to_tracking": list(self.keyboard_to_tracking),
            "tracking_usable": self.tracking_usable,
            "key_highlights": [item.to_dict() for item in self.key_highlights],
        }


@dataclass(frozen=True, slots=True)
class AdapterSnapshot:
    status: str
    reason: str | None
    state: AdaptedSceneState | None


@dataclass(frozen=True, slots=True)
class AdapterDecision:
    accepted: bool
    reason: str
    filtered_unknown_key_ids: tuple[str, ...]
    snapshot: AdapterSnapshot


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StateContractError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise StateContractError(f"{field} must be finite")
    return result


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StateContractError(f"{field} must be an object")
    return value


def _json_array(value: object, *, field: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise StateContractError(f"{field} must be an array")
    return value


class SceneStateAdapter:
    """Consume a versioned state stream while retaining only its newest frame."""

    def __init__(
        self,
        *,
        revision_gate: RevisionGate,
        known_key_ids: Iterable[str],
        ttl_ms: float = 250.0,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        fixed_transform: Sequence[float] = DEFAULT_DEMO_FIXED_TRANSFORM,
    ) -> None:
        keys = tuple(known_key_ids)
        if not keys or any(not isinstance(key, str) or not key for key in keys):
            raise ValueError("known_key_ids must contain non-empty strings")
        if len(keys) != len(set(keys)):
            raise ValueError("known_key_ids must be unique")
        if isinstance(ttl_ms, bool) or not isinstance(ttl_ms, (int, float)):
            raise ValueError("ttl_ms must be a positive finite number")
        ttl_value = float(ttl_ms)
        if not math.isfinite(ttl_value) or ttl_value <= 0:
            raise ValueError("ttl_ms must be a positive finite number")
        transform = tuple(float(value) for value in fixed_transform)
        if len(transform) != 12 or not all(math.isfinite(value) for value in transform):
            raise ValueError("fixed_transform must contain twelve finite values")

        self.revision_gate = revision_gate
        self.known_key_ids = frozenset(keys)
        self.ttl_ns = int(ttl_value * 1_000_000)
        self._clock_ns = clock_ns
        self.fixed_transform = transform
        self._lock = RLock()
        self._active_session_id: str | None = None
        self._retired_session_ids: set[str] = set()
        self._last_sequence = -1
        self._source_id: str | None = None
        self._latest: AdaptedSceneState | None = None
        self._latest_received_at_ns: int | None = None
        self._status = "empty"
        self._reason: str | None = None

    @property
    def active_session_id(self) -> str | None:
        with self._lock:
            return self._active_session_id

    @property
    def last_sequence(self) -> int:
        with self._lock:
            return self._last_sequence

    def _snapshot_locked(self, now_ns: int) -> AdapterSnapshot:
        if self._latest is not None and self._latest_received_at_ns is not None:
            elapsed = max(0, now_ns - self._latest_received_at_ns)
            if elapsed >= self.ttl_ns:
                self._latest = None
                self._latest_received_at_ns = None
                self._status = "stale"
                self._reason = "latest accepted state exceeded its TTL"
        return AdapterSnapshot(self._status, self._reason, self._latest)

    def snapshot(self, *, now_ns: int | None = None) -> AdapterSnapshot:
        checked_at = self._clock_ns() if now_ns is None else now_ns
        if not _is_nonnegative_int(checked_at):
            raise ValueError("now_ns must be a non-negative integer")
        with self._lock:
            return self._snapshot_locked(checked_at)

    def _clear_locked(self, *, status: str, reason: str) -> None:
        self._latest = None
        self._latest_received_at_ns = None
        self._status = status
        self._reason = reason

    def _decision_locked(
        self,
        *,
        accepted: bool,
        reason: str,
        filtered_unknown_key_ids: tuple[str, ...] = (),
        now_ns: int,
    ) -> AdapterDecision:
        return AdapterDecision(
            accepted=accepted,
            reason=reason,
            filtered_unknown_key_ids=filtered_unknown_key_ids,
            snapshot=self._snapshot_locked(now_ns),
        )

    def ingest(
        self,
        state: Mapping[str, Any],
        *,
        session_id: str,
        sequence: int,
        received_at_ns: int | None = None,
    ) -> AdapterDecision:
        """Validate and atomically replace the current renderer state.

        Duplicate and out-of-order sequences are ignored without disturbing a
        newer state.  A new session permits sequence zero but first clears the
        old session.  Contract or revision errors advance the sequence and
        clear the render state so stale highlights cannot survive.
        """

        now_ns = self._clock_ns() if received_at_ns is None else received_at_ns
        if not _is_nonnegative_int(now_ns):
            raise ValueError("received_at_ns must be a non-negative integer")

        with self._lock:
            self._snapshot_locked(now_ns)
            if not isinstance(session_id, str) or not session_id:
                self._clear_locked(status="blocked", reason="invalid session_id")
                return self._decision_locked(
                    accepted=False,
                    reason="invalid session_id",
                    now_ns=now_ns,
                )
            if not _is_nonnegative_int(sequence):
                self._clear_locked(status="blocked", reason="invalid sequence")
                return self._decision_locked(
                    accepted=False,
                    reason="invalid sequence",
                    now_ns=now_ns,
                )

            if self._active_session_id is None:
                self._active_session_id = session_id
            elif session_id != self._active_session_id:
                if session_id in self._retired_session_ids:
                    return self._decision_locked(
                        accepted=False,
                        reason="retired session ignored",
                        now_ns=now_ns,
                    )
                self._retired_session_ids.add(self._active_session_id)
                self._active_session_id = session_id
                self._last_sequence = -1
                self._source_id = None
                self._clear_locked(status="empty", reason="session reset")

            if sequence <= self._last_sequence:
                return self._decision_locked(
                    accepted=False,
                    reason="duplicate or out-of-order sequence ignored",
                    now_ns=now_ns,
                )

            # Claim the sequence before decoding.  A later-valid but older
            # packet must not resurrect content cleared by a bad newer packet.
            self._last_sequence = sequence
            try:
                adapted, unknown_ids = self._adapt_state(
                    state,
                    session_id=session_id,
                    sequence=sequence,
                    received_at_ns=now_ns,
                )
            except (StateContractError, TypeError, ValueError, OverflowError) as exc:
                reason = f"state rejected: {exc}"
                self._clear_locked(status="blocked", reason=reason)
                return self._decision_locked(
                    accepted=False,
                    reason=reason,
                    now_ns=now_ns,
                )

            if adapted is None:
                self._clear_locked(
                    status="unavailable",
                    reason="SceneState contains no keyboard state",
                )
                return self._decision_locked(
                    accepted=True,
                    reason="valid unavailable state cleared the renderer",
                    now_ns=now_ns,
                )

            if self._source_id is None:
                self._source_id = adapted.source_id
            elif adapted.source_id != self._source_id:
                reason = "state rejected: source_id changed without a session reset"
                self._clear_locked(status="blocked", reason=reason)
                return self._decision_locked(
                    accepted=False,
                    reason=reason,
                    now_ns=now_ns,
                )

            self._latest = adapted
            self._latest_received_at_ns = now_ns
            self._status = "ready"
            self._reason = None
            return self._decision_locked(
                accepted=True,
                reason="accepted latest state",
                filtered_unknown_key_ids=unknown_ids,
                now_ns=now_ns,
            )

    def _adapt_state(
        self,
        state: Mapping[str, Any],
        *,
        session_id: str,
        sequence: int,
        received_at_ns: int,
    ) -> tuple[AdaptedSceneState | None, tuple[str, ...]]:
        payload = _mapping(state, field="SceneState")
        if set(payload) != _TOP_LEVEL_FIELDS:
            missing = sorted(_TOP_LEVEL_FIELDS - set(payload))
            extra = sorted(set(payload) - _TOP_LEVEL_FIELDS)
            raise StateContractError(
                f"SceneState fields do not match 0.2 (missing={missing}, extra={extra})"
            )
        if payload.get("schema_version") != SCENE_STATE_SCHEMA_VERSION:
            raise StateContractError("schema_version must be 0.2")

        source_id = payload.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise StateContractError("source_id must be a non-empty string")
        source_frame_id = payload.get("source_frame_id")
        captured_at_ns = payload.get("captured_at_ns")
        emitted_at_ns = payload.get("emitted_at_ns")
        for field, value in (
            ("source_frame_id", source_frame_id),
            ("captured_at_ns", captured_at_ns),
            ("emitted_at_ns", emitted_at_ns),
        ):
            if not _is_nonnegative_int(value):
                raise StateContractError(f"{field} must be a non-negative integer")

        _json_array(payload.get("hands"), field="hands")
        _json_array(payload.get("fingertips"), field="fingertips")
        _mapping(payload.get("diagnostics"), field="diagnostics")

        keyboard_value = payload.get("keyboard")
        if keyboard_value is None:
            return None, ()
        keyboard = _mapping(keyboard_value, field="keyboard")
        if keyboard.get("coordinate_space") != "aruco-anchor-reference-2d":
            raise StateContractError("unsupported keyboard coordinate_space")
        if keyboard.get("key_semantics") != KEY_CANDIDATE_SEMANTICS:
            raise StateContractError("unsupported keyboard key_semantics")

        artifacts = _mapping(keyboard.get("artifacts"), field="keyboard.artifacts")
        expected = self.revision_gate.to_dict()
        for field, revision in expected.items():
            if artifacts.get(field) != revision:
                raise StateContractError(f"{field} revision mismatch")

        model = _mapping(keyboard.get("model"), field="keyboard.model")
        if model.get("revision") != expected["model"]:
            raise StateContractError("keyboard.model revision mismatch")
        if model.get("node_prefix") != "key:":
            raise StateContractError("keyboard.model node_prefix must be key:")
        if model.get("key_count") != len(self.known_key_ids):
            raise StateContractError("keyboard.model key_count mismatch")

        pose = _mapping(keyboard.get("pose"), field="keyboard.pose")
        usable = pose.get("usable")
        if not isinstance(usable, bool):
            raise StateContractError("keyboard.pose.usable must be boolean")

        highlights_raw = _json_array(
            payload.get("key_highlights"), field="key_highlights"
        )
        alias_raw = _json_array(payload.get("hovered_keys"), field="hovered_keys")
        if list(highlights_raw) != list(alias_raw):
            raise StateContractError("hovered_keys must equal key_highlights in 0.2")

        highlights: list[AdaptedHighlight] = []
        unknown_ids: list[str] = []
        seen_known: set[str] = set()
        seen_unknown: set[str] = set()
        if usable:
            for index, raw in enumerate(highlights_raw):
                item = _mapping(raw, field=f"key_highlights[{index}]")
                key_id = item.get("physical_key_id")
                if not isinstance(key_id, str) or not key_id:
                    raise StateContractError(
                        f"key_highlights[{index}].physical_key_id is invalid"
                    )
                if key_id not in self.known_key_ids:
                    if key_id not in seen_unknown:
                        seen_unknown.add(key_id)
                        unknown_ids.append(key_id)
                    continue
                if key_id in seen_known:
                    raise StateContractError(f"duplicate highlight for {key_id}")
                seen_known.add(key_id)
                expected_node = f"key:{key_id}"
                if item.get("model_node_id") != expected_node:
                    raise StateContractError(f"model node mismatch for {key_id}")
                label = item.get("label")
                if not isinstance(label, str):
                    raise StateContractError(f"label for {key_id} must be a string")
                intensity = _finite_number(
                    item.get("intensity"), field=f"{key_id}.intensity"
                )
                if intensity < 0.0 or intensity > 1.0:
                    raise StateContractError(
                        f"{key_id}.intensity must be between zero and one"
                    )
                direct = item.get("direct")
                if not isinstance(direct, bool):
                    raise StateContractError(f"{key_id}.direct must be boolean")
                contributors_raw = _json_array(
                    item.get("contributors"), field=f"{key_id}.contributors"
                )
                contributors = tuple(contributors_raw)
                if any(
                    not isinstance(contributor, str) or not contributor
                    for contributor in contributors
                ):
                    raise StateContractError(
                        f"{key_id}.contributors must contain non-empty strings"
                    )
                if len(contributors) != len(set(contributors)):
                    raise StateContractError(f"{key_id}.contributors must be unique")
                highlights.append(
                    AdaptedHighlight(
                        physical_key_id=key_id,
                        model_node_id=expected_node,
                        label=label,
                        intensity=intensity,
                        direct=direct,
                        contributors=contributors,
                    )
                )

        return (
            AdaptedSceneState(
                session_id=session_id,
                sequence=sequence,
                source_id=source_id,
                source_frame_id=source_frame_id,
                captured_at_ns=captured_at_ns,
                emitted_at_ns=emitted_at_ns,
                received_at_ns=received_at_ns,
                pose_source=POSE_SOURCE_DEMO_FIXED,
                keyboard_to_tracking=self.fixed_transform,
                tracking_usable=usable,
                key_highlights=tuple(highlights),
            ),
            tuple(unknown_ids),
        )


__all__ = [
    "ADAPTED_STATE_SCHEMA_VERSION",
    "AdapterDecision",
    "AdapterSnapshot",
    "AdaptedHighlight",
    "AdaptedSceneState",
    "DEFAULT_DEMO_FIXED_TRANSFORM",
    "POSE_SOURCE_DEMO_FIXED",
    "RevisionGate",
    "SCENE_STATE_SCHEMA_VERSION",
    "SceneStateAdapter",
    "StateContractError",
]
