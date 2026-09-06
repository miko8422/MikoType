from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import threading
import time

import numpy as np
import pytest

from deskvision.core.config import ArtifactConfig, load_config
from deskvision.core.models import FramePacket
from deskvision.perception.hand_base import HandTrackingResult
from deskvision.calibration.artifacts import load_keyboard_calibration_artifacts
from deskvision.runtime import RuntimeBuildError, build_runtime, ensure_keyboard_model


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


class _Source:
    def __init__(self) -> None:
        self._open = False
        self._frame_id = 0
        self.closed = threading.Event()

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        self._open = True

    def read(self) -> FramePacket | None:
        if not self._open:
            return None
        self._frame_id += 1
        time.sleep(0.003)
        return FramePacket(
            "test_camera",
            self._frame_id,
            time.time_ns(),
            32,
            24,
            np.zeros((24, 32, 3), dtype=np.uint8),
        )

    def close(self) -> None:
        self._open = False
        self.closed.set()


class _NoHands:
    model_id = "fake_hands"

    def __init__(self) -> None:
        self.closed = False

    def track(self, frame: FramePacket) -> HandTrackingResult:
        started = time.monotonic_ns()
        return HandTrackingResult(
            self.model_id,
            frame.source_id,
            frame.frame_id,
            frame.acquired_at_ns,
            started,
            time.monotonic_ns(),
            (),
        )

    def close(self) -> None:
        self.closed = True


class _BlockingHands(_NoHands):
    """Keep one perception call in flight until a shutdown test releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.tracking = threading.Event()
        self.close_calls = 0
        self.closed_while_tracking = False

    def track(self, frame: FramePacket) -> HandTrackingResult:
        self.tracking.set()
        self.entered.set()
        try:
            if not self.release.wait(timeout=2.0):
                raise TimeoutError("test did not release blocked hand tracking")
            return super().track(frame)
        finally:
            self.tracking.clear()

    def close(self) -> None:
        self.close_calls += 1
        self.closed_while_tracking = self.tracking.is_set()
        super().close()


class _CloseFailsOnceHands(_NoHands):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            raise RuntimeError("synthetic tracker close failure")
        super().close()


class _NoMarkers:
    def detect(self, frame: FramePacket):
        return {}


def test_runtime_composes_real_artifacts_and_latest_frame_workers(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs/dev.yaml")
    artifacts = replace(
        config.artifacts,
        model_glb=tmp_path / "keyboard.glb",
        model_manifest=tmp_path / "keyboard_manifest.json",
    )
    config = replace(config, artifacts=artifacts)
    source = _Source()
    runtime = build_runtime(
        config,
        source=source,
        hand_tracker=_NoHands(),
        marker_detector=_NoMarkers(),
    )

    runtime.start()
    deadline = time.monotonic() + 2.0
    while runtime.states.latest() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    runtime.stop()

    state = runtime.states.latest()
    assert state is not None
    assert state.source_id == "test_camera"
    assert state.keyboard.artifacts.contact_map == runtime.artifacts.contact_map.revision
    assert state.keyboard.model.key_count == 82
    assert state.key_highlights == ()
    assert source.closed.is_set()
    assert artifacts.model_glb.read_bytes().startswith(b"glTF")
    assert artifacts.model_manifest.is_file()

    # The composition root is intentionally one-shot: shutdown is idempotent,
    # but a closed MediaPipe instance can never be restarted accidentally.
    runtime.stop()
    assert runtime.status == "closed"
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        runtime.start()


def test_runtime_start_failure_closes_pipeline_and_source(tmp_path: Path) -> None:
    class _OpenFailureSource(_Source):
        def open(self) -> None:
            raise RuntimeError("camera unavailable")

    config = load_config(ROOT / "configs/dev.yaml")
    config = replace(
        config,
        artifacts=replace(
            config.artifacts,
            model_glb=tmp_path / "keyboard.glb",
            model_manifest=tmp_path / "keyboard_manifest.json",
        ),
    )
    source = _OpenFailureSource()
    hands = _NoHands()
    runtime = build_runtime(
        config,
        source=source,
        hand_tracker=hands,
        marker_detector=_NoMarkers(),
    )

    with pytest.raises(Exception, match="camera unavailable"):
        runtime.start()

    assert source.closed.is_set()
    assert hands.closed is True
    assert runtime.is_running is False


def test_runtime_stop_timeout_defers_pipeline_close_until_worker_exits(
    tmp_path: Path,
) -> None:
    config = load_config(ROOT / "configs/dev.yaml")
    config = replace(
        config,
        artifacts=replace(
            config.artifacts,
            model_glb=tmp_path / "keyboard.glb",
            model_manifest=tmp_path / "keyboard_manifest.json",
        ),
        pipeline=replace(config.pipeline, stop_timeout_s=0.02),
    )
    source = _Source()
    hands = _BlockingHands()
    runtime = build_runtime(
        config,
        source=source,
        hand_tracker=hands,
        marker_detector=_NoMarkers(),
    )

    runtime.start()
    assert hands.entered.wait(timeout=1.0)
    try:
        with pytest.raises(RuntimeError, match="runtime shutdown failed") as exc_info:
            runtime.stop()

        assert "PerceptionWorkerStopError" in str(exc_info.value)
        assert runtime.status == "shutdown_failed"
        assert runtime.is_running is True
        assert hands.closed is False
        assert hands.close_calls == 0
        assert hands.closed_while_tracking is False
    finally:
        hands.release.set()
        deadline = time.monotonic() + 1.0
        while runtime.perception.is_running and time.monotonic() < deadline:
            time.sleep(0.005)
        runtime.stop()

    assert runtime.status == "closed"
    assert runtime.is_running is False
    assert hands.closed is True
    assert hands.close_calls == 1
    assert hands.closed_while_tracking is False


def test_runtime_retries_failed_pipeline_close_without_allowing_restart(
    tmp_path: Path,
) -> None:
    config = load_config(ROOT / "configs/dev.yaml")
    config = replace(
        config,
        artifacts=replace(
            config.artifacts,
            model_glb=tmp_path / "keyboard.glb",
            model_manifest=tmp_path / "keyboard_manifest.json",
        ),
    )
    hands = _CloseFailsOnceHands()
    runtime = build_runtime(
        config,
        source=_Source(),
        hand_tracker=hands,
        marker_detector=_NoMarkers(),
    )
    runtime.start()

    with pytest.raises(RuntimeError, match="synthetic tracker close failure"):
        runtime.stop()

    assert runtime.status == "shutdown_failed"
    assert runtime.is_running is False
    assert hands.closed is False
    with pytest.raises(RuntimeError, match="shutdown_failed"):
        runtime.start()

    runtime.stop()
    assert runtime.status == "closed"
    assert hands.closed is True
    assert hands.close_calls == 2


def test_model_check_mode_is_read_only_and_rejects_missing_output(
    tmp_path: Path,
) -> None:
    artifacts = load_keyboard_calibration_artifacts(
        layout_path=ROOT / "data/keyboards/kzzi_user_adjustable_82/layout.json",
        anchor_path=ROOT / "data/keyboards/kzzi_user_adjustable_82/anchor_reference.json",
        contact_map_path=ROOT / "data/keyboards/kzzi_user_adjustable_82/contact_map.json",
    )
    model = tmp_path / "missing.glb"
    manifest = tmp_path / "missing.json"

    with pytest.raises(RuntimeBuildError, match="does not match"):
        ensure_keyboard_model(
            artifacts,
            model_path=model,
            manifest_path=manifest,
            repair=False,
        )

    assert not model.exists()
    assert not manifest.exists()


def test_model_repair_replaces_manifest_with_exact_generated_snapshot(
    tmp_path: Path,
) -> None:
    artifacts = load_keyboard_calibration_artifacts(
        layout_path=ROOT / "data/keyboards/kzzi_user_adjustable_82/layout.json",
        anchor_path=ROOT / "data/keyboards/kzzi_user_adjustable_82/anchor_reference.json",
        contact_map_path=ROOT / "data/keyboards/kzzi_user_adjustable_82/contact_map.json",
    )
    model = tmp_path / "keyboard.glb"
    manifest = tmp_path / "manifest.json"
    generated = ensure_keyboard_model(
        artifacts,
        model_path=model,
        manifest_path=manifest,
    )
    tampered = json.loads(manifest.read_text(encoding="utf-8"))
    tampered["untrusted_extra"] = True
    manifest.write_text(json.dumps(tampered), encoding="utf-8")

    ensure_keyboard_model(
        artifacts,
        model_path=model,
        manifest_path=manifest,
    )

    assert json.loads(manifest.read_text(encoding="utf-8")) == generated.manifest
