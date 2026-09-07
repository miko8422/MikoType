# Windows-Local Production Control Console

The production runtime exposes one local FastAPI control plane on one loopback
port. Its three browser pages have different authority but share the same
camera, perception worker, exact-frame stores, and immutable live artifacts.

`deskvision run` serves a read-only inspector at `/`. The browser receives one
single-slot `FramePacket + SceneState` bundle over `/ws/bundle`: metadata/state
first, then the JPEG made from that exact processed frame. It commits the image,
fingertip overlay, and key highlights only after source ID, frame ID, timestamp,
byte length, and image decode agree. Disconnect, stale timeout, or perception
failure immediately clears the visual state.

The algorithm does not consume this JPEG/WebSocket path. MediaPipe, ArUco, and
Contact Map evaluation share the raw BGR frame in process; browser encoding is
only a diagnostic side branch.

`/settings` exposes an allowlisted configuration editor. It never exposes the
binding host, deployment topology, artifact paths, remote inference, tokens, or
the MediaPipe acknowledgement. Valid changes are written atomically to the
gitignored sibling `*.local.yaml` override and are explicitly marked as needing
a restart; construction-time camera and inference objects are not hot-swapped.

`/setup` and its mutating API are always mounted on the same service. Setup is
restricted to loopback, has no image upload, keeps Anchor polling single-flight,
and cancels delayed contact capture on Escape, window blur, or a hidden page.
New registration invalidates older staging Anchor/Contact files. Marker cards
come from the user's active layout rather than a hard-coded ID range. Every
request is pinned to the actual loopback Host; browser mutations and
WebSockets carrying a foreign Origin are rejected before they can change setup
state or read camera bundles. Cross-site subresources are rejected and camera
responses opt into same-origin resource protection. The setup workspace is
initialized lazily and cannot overlap production artifacts, so an unavailable
or mistaken staging path cannot damage the active keyboard bundle.

Production artifact responses are immutable snapshots owned by the running
process. Applying a new bundle changes disk state but cannot mix a new GLB with
old live SceneState; the page tells the operator to restart before the new
revision becomes active.

The reserved experimental remote-inference contract is intentionally not
mounted here. This inspector has no network authentication and must never be
exposed as a cross-device camera service.

The preferred port is reserved before camera startup. A fixed-port conflict
fails with an actionable error; `--auto-port` explicitly opts into a bounded
search and the actual endpoint is available from `/api/service`. If the fixed
endpoint identifies itself as the same MikoType config revision and Setup
workspace, `run`/`setup` reuse it instead of opening a second camera runtime.
An incompatible instance is never silently reused. SteamVR production
integration should keep a fixed port unless it implements endpoint discovery.
