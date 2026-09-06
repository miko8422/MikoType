# Contracts

This directory contains versioned, transport-safe contracts shared by the Mac vision node, browser presenter, and future Windows VR client.

`scene_state.schema.json` describes SceneState schema version `0.2`. Raw image
arrays belong only to in-process `FramePacket` objects and must never be
embedded in SceneState.

Production hand tracking uses the typed `HandTrackingResult` contract under
`src/deskvision/perception/hand_base.py`. Its serialized schema identifier is
`hand-tracking-0.1`; only each hand's transport-safe handedness, score, 21 image
landmarks, and optional 21 world landmarks enter `SceneState.hands`.

Schema 0.2 adds revision-gated keyboard pose/model metadata, per-fingertip
ranked physical-key candidates, aggregated `key_highlights`, and latency/health
diagnostics. `hovered_keys` is a temporary compatibility alias containing the
same highlight records. Candidate semantics are explicitly
`likely-contact-not-mechanical-keypress`.
