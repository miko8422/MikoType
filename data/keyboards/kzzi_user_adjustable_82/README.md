# Kzzi User-Adjusted 82-Key Production Bundle

This directory is the current production keyboard bundle promoted from the
completed user layout and contact calibration.

- `layout.json`: 82 physical keys, nominal drawing/3D geometry, marker
  assignments, and calibration order.
- `anchor_reference.json`: the measured ArUco reference frame.
- `contact_map.json`: 410 samples, exactly five for each of the 82 keys.
- `adaptive_keyboard.glb`: generated self-contained model with one
  `key:<physical_key_id>` node per key.
- `adaptive_keyboard_manifest.json`: model digest plus layout, inventory,
  Anchor, and Contact Map revisions.

These files form one revision-gated set. Do not hand-edit one file in isolation;
use the production setup flow or `deskvision build-keyboard` so the whole set is
validated and regenerated together.
