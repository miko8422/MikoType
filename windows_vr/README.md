# Windows VR Client

This directory is the boundary for the same-host Windows SteamVR consumer.
Camera, inference, keyboard mapping, FastAPI, and this future consumer all run
on one Windows x64 PC in the V0.1 topology.

The live implementation now lives in
[`integrations/steamvr`](../integrations/steamvr/README.md), separately built
for Windows. The shared Python adapter is `src/deskvision/steamvr`; its unified
control/diagnostic page is `/steamvr` on the selected service port. This
directory remains a compatibility pointer, not a second implementation.

The isolated source-only feasibility work lives under
[`demo/steamvr_home_hybrid`](../demo/steamvr_home_hybrid/README.md). Its
fixed-pose GenericTracker source and static Overlay smoke do not count as a
live consumer. Production integration adds latest-frame transport, manual
standing-space pose, dynamic Overlay, current-model hash gating and bounded
logs. Windows x64 + SteamVR Home + HMD acceptance remains a separate requirement.

The distributed Windows-camera-to-remote/macOS-inference route is a separate,
disabled experiment documented under `contracts/`; it is not this V0.1 path.
