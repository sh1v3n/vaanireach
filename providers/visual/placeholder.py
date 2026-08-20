"""Shared Tier-2 local placeholder card — used by every `VisualProvider`
implementation (`GeminiImagenProvider`, `HuggingFaceVisualProvider`, and
any future one) when its real image-generation backend is unavailable.
Factored out so every provider's "never crash the demo" fallback draws
the exact same card rather than each reimplementing it slightly
differently.
"""
from __future__ import annotations

import textwrap

from providers.visual.local_cache import LocalCache

_PLACEHOLDER_SIZE = (768, 1365)  # ~9:16, close enough for a fallback card
_PLACEHOLDER_BG = (30, 41, 59)  # slate-800 — neutral, readable with white text
_PLACEHOLDER_FG = (241, 245, 249)  # slate-100


def write_placeholder_card(cache: LocalCache, asset_id: str, prompt: str) -> str:
    """Draws a plain slate-colored card with the (wrapped) prompt text so
    the demo still has *something* in the right aspect ratio for every
    B-roll slot, and reviewers can immediately tell it's a stand-in
    rather than mistaking it for a real generation. Written under the
    cache root but with a `_placeholder_` prefix so it can never collide
    with (or be mistaken for) a real cached hit."""
    from PIL import Image, ImageDraw  # local import: keep Pillow off the hot path when generation succeeds

    out_path = cache.root / f"_placeholder_{asset_id}.jpg"
    img = Image.new("RGB", _PLACEHOLDER_SIZE, color=_PLACEHOLDER_BG)
    draw = ImageDraw.Draw(img)
    wrapped = textwrap.fill(prompt, width=28)
    text_bbox = draw.multiline_textbbox((0, 0), wrapped, spacing=10)
    text_w, text_h = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
    x = max(0, (_PLACEHOLDER_SIZE[0] - text_w) // 2)
    y = max(0, (_PLACEHOLDER_SIZE[1] - text_h) // 2)
    draw.multiline_text((x, y), wrapped, fill=_PLACEHOLDER_FG, spacing=10, align="center")
    img.save(out_path, format="JPEG", quality=85)
    return str(out_path)
