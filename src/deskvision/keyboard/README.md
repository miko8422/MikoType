# Production Adaptive Keyboard

This package converts validated user calibration into a deterministic,
self-contained GLB and translates fingertip candidates into renderable key
highlight state.

`generate_adaptive_keyboard_model()` uses the user's nominal layout only for
visible keycap position and size. It creates one independent node named
`key:<physical_key_id>` for every inventory key. The finalized Contact Map is
embedded by revision but remains the authority for physical interaction.

The interaction layer emits one direct candidate and optional weaker neighbors.
When several fingertips contribute to the same key, intensity is the maximum,
`direct` is combined with logical OR, and contributor IDs are deduplicated. The
result is visual likelihood only; this module does not emit keyboard events.

Build or validate a standalone deployable bundle with:

```bash
uv run --locked python -m deskvision.main build-keyboard \
  --layout path/to/layout.json \
  --anchor path/to/anchor_reference.json \
  --contact-map path/to/contact_map.json \
  --output path/to/new_bundle
```

This uses the root README's recommended uv environment. In an activated Conda
environment, run the command without `uv run --locked`.

Input and output paths must be distinct so a bad configuration cannot overwrite
source calibration data.
