"""Production keyboard modeling and interaction algorithms."""

from .adaptive_model import (
    AdaptiveKeyboardModelError,
    GeneratedKeyboardModel,
    generate_adaptive_keyboard_model,
    layout_geometry_revision,
)
from .bundle import KeyboardBundlePaths, KeyboardBundleResult, build_keyboard_bundle
from .interaction import (
    FingertipBubble,
    KeyHighlight,
    KeyboardInteractionConfig,
    KeyboardPlane,
    KeyboardRect,
    aggregate_key_highlights,
    evaluate_bubble_highlights,
    fingertip_bubbles,
    highlights_from_key_candidates,
)


__all__ = [
    "AdaptiveKeyboardModelError",
    "FingertipBubble",
    "GeneratedKeyboardModel",
    "KeyHighlight",
    "KeyboardInteractionConfig",
    "KeyboardBundlePaths",
    "KeyboardBundleResult",
    "KeyboardPlane",
    "KeyboardRect",
    "aggregate_key_highlights",
    "build_keyboard_bundle",
    "evaluate_bubble_highlights",
    "fingertip_bubbles",
    "generate_adaptive_keyboard_model",
    "highlights_from_key_candidates",
    "layout_geometry_revision",
]
