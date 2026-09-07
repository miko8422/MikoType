# Production Keyboard Calibration

This package owns the production artifacts that turn one user's physical
keyboard into a stable reference-space map. It never imports a Demo module.

The artifacts have separate responsibilities:

- `layout.json` declares which keys exist, their calibration order, marker
  assignments, and nominal rectangles used for editing and 3D appearance.
- `anchor_reference.json` measures the sparse ArUco marker geometry and defines
  the keyboard reference coordinate system.
- `contact_map.json` stores five measured right-index samples per key. Its
  medians, rather than layout rectangles, drive physical key candidates.

Every artifact has a content-derived revision. Inventory, Anchor Reference,
and Contact Map revisions must agree before runtime or bundle generation can
start. Mismatches fail closed and produce no key highlights.

`KeyboardSetupController` is available only through the explicit `setup`
command. It writes drafts into a staging workspace, supports resumable contact
calibration, and applies a validated bundle to the configured production paths
only after the user chooses Apply. The active process must then be restarted so
one runtime never mixes old state with a new calibration.

```powershell
uv run --locked mikotype setup `
  --config configs\windows.yaml `
  --acknowledge-mediapipe-metrics
```

This follows the root README's recommended uv setup. In an activated Conda
environment, omit `uv run --locked`.

The setup page uses the camera directly; it has no image-upload control. The
current V0.1 calibration contract describes likely fingertip contact, not proof
that a physical switch travelled or produced an operating-system key event.
