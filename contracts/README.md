# Contracts

This directory contains versioned, transport-safe contracts for MikoType's
same-host Windows vision, browser, and future SteamVR components.

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

`remote_inference.schema.json` reserves the disabled experimental boundary for
a possible Windows-camera-to-remote-inference route. A JSON `frame_header` is
followed by one bounded binary JPEG, and the returned `scene_state` envelope
must match the same session, sequence, source, and frame ID. Authentication is
transport metadata, never a JSON field or URL query. The V0.1 runtime does not
register the reserved `/ws/experimental/inference` route or activate the
`mikotype.remote-inference.v0.1` WebSocket subprotocol.
