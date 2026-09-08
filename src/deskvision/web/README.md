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

The configured port is preferred rather than forcibly claimed. By default,
`run` and `setup` probe and reserve up to 20 consecutive loopback ports starting
there (8765 through 8784 with the default configuration). A matching MikoType
config revision and Setup workspace anywhere in that range is reused instead
of opening a second camera runtime; otherwise the first free candidate is
reserved before camera startup. If the whole range is unavailable, startup
fails without opening the camera. `--strict-port` disables fallback when an
integration genuinely requires the preferred port.

No occupied-port owner is terminated or reconfigured. Pimax software and all
other local services remain running while MikoType continues the scan. Service
identity probes connect directly to loopback and bypass inherited HTTP(S) proxy
settings only for those requests; they do not change the system proxy, VPN,
routes, or another application's networking.

Startup and reuse both print `OPEN THIS EXACT URL: ...`; operators should open
only that address instead of assuming the preferred port was selected. The
actual endpoint is also reported by `/api/service`. SteamVR production
integration must either perform the same bounded loopback discovery and verify
each candidate's identity through `/api/service`, or opt into `--strict-port`
and fail closed. `/api/service` verifies a known candidate; it does not reveal
an otherwise unknown port by itself.
