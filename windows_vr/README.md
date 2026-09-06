# Windows VR Client

This directory is the boundary for the same-host Windows SteamVR consumer.
Camera, inference, keyboard mapping, FastAPI, and this future consumer all run
on one Windows x64 PC in the V0.1 topology.

No live SteamVR/OpenVR consumer has been promoted into V0.1 yet. The future
component should read `SceneState 0.2` and the adaptive model from the loopback
FastAPI service, fail closed on stale/revision-mismatched state, and keep
network or JSON work outside `vrserver` callbacks.

The isolated source-only feasibility work lives under
[`demo/steamvr_home_hybrid`](../demo/steamvr_home_hybrid/README.md). Its
fixed-pose GenericTracker source and static Overlay smoke do not count as a
live consumer. Promotion into this directory requires dynamic loopback state
consumption plus Windows x64 + SteamVR + HMD acceptance.

The distributed Windows-camera-to-remote/macOS-inference route is a separate,
disabled experiment documented under `contracts/`; it is not this V0.1 path.
