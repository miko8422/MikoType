# Production Local Web Interfaces

The production runtime has two local interfaces with different authority.

`deskvision run` serves a read-only inspector at `/`. The browser receives one
single-slot `FramePacket + SceneState` bundle over `/ws/bundle`: metadata/state
first, then the JPEG made from that exact processed frame. It commits the image,
fingertip overlay, and key highlights only after source ID, frame ID, timestamp,
byte length, and image decode agree. Disconnect, stale timeout, or perception
failure immediately clears the visual state.

The algorithm does not consume this JPEG/WebSocket path. MediaPipe, ArUco, and
Contact Map evaluation share the raw BGR frame in process; browser encoding is
only a diagnostic side branch.

`deskvision setup` additionally mounts `/setup` and its mutating API. Setup is
restricted to a loopback host, has no image upload, keeps Anchor polling
single-flight, and cancels delayed contact capture on Escape, window blur, or a
hidden page. New registration invalidates older staging Anchor/Contact files.

Production artifact responses are immutable snapshots owned by the running
process. Applying a new bundle changes disk state but cannot mix a new GLB with
old live SceneState; the page tells the operator to restart before the new
revision becomes active.
