# Archived and local-only material

`dispose/` holds confirmed unused scaffolding and recoverable local artifacts.
It is not a Python package, a Demo, a runtime data directory, or an installation
source. Do not add it to `PYTHONPATH` or import it from production code.

The package build discovers only `deskvision` under `src/`. The architecture
test rejects production imports from `demo`, `tests`, or `dispose`.

## Archived source inventory

| Original path | Current path | Reason |
| --- | --- | --- |
| `src/deskvision/perception/noop.py` | `legacy_scaffold/src/deskvision/perception/noop.py` | The original empty perception implementation returned `{}`. No runtime, Demo, or behavioral test used it; only the scaffold import list referred to it. The active pipeline uses the hand tracker, ArUco locator, and contact mapper. |
| `src/deskvision/observability/metrics.py` | `legacy_scaffold/src/deskvision/observability/metrics.py` | These three field-name constants had no consumers. Capture health and `SceneState.Diagnostics` already own the actual metrics contracts. |

The archived implementations remain available for reference. To restore one,
move it to its original path, establish a concrete caller and behavioral test,
then update this inventory. Merely importing an unused module is not evidence
that it belongs in production.

## Recoverable local artifacts

The following are local-only and excluded from Git by
`dispose/local_artifacts/`. They are intentionally absent from other clones.

| Original path | Local archive path | Reason |
| --- | --- | --- |
| `build/` | `local_artifacts/2026-09-09/build/` | Stale generated packaging tree from August 28. Its entry point reported only Task 1 and it contained old `mac_camera` / `remote_stub` modules. Retaining it in a build directory can contaminate subsequent incremental builds with removed source files. |
| `:memory:.ses` | `local_artifacts/2026-09-09/mediapipe-memory-session.ses` | Local MediaPipe session artifact, unrelated to production source. The archive name is Windows-compatible. Its contents are not committed. |

These entries were relocated without deletion. For forensic inspection, use
their archive paths. Restore the build tree only to a separate temporary
directory; never put an old build on the application's import path. A new
package build regenerates `build/` from current source. The current model asset
under `src/deskvision/perception/assets/` remains in place; the archived build
contains only its old generated copy. Environments, calibration data, Demo
outputs, and model caches were not moved.

## Features deliberately retained

- Production video capture, MediaPipe hands, ArUco localization, contact
  calibration, adaptive 3D keyboard, key highlights, and configuration WebUI.
- The disabled experimental remote-inference contract requested for a future
  Windows-camera / remote-model topology.
- The SteamVR Home hybrid Demo and all earlier model, keyboard, layout, and
  calibration experiments under `demo/`.
- Automated and manual test tools under `tests/`; Demo dependencies and test
  environments stay separate from production installation.
- The small lifecycle, frame-preprocessing, and homography protocols, which
  describe previously planned extension points.

Only confirmed unused scaffolding is archived here. A feature is not redundant
merely because its hardware acceptance or later integration is unfinished.
