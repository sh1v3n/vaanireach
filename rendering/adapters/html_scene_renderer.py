"""HtmlSceneRenderer — the guaranteed SceneRenderer implementation for
TEXT/INFOGRAPHIC/MAP scenes (Phase 2 media plan). Renders a small themed
HTML page per scene and screenshots it with Playwright (headless
Chromium, local, free, no API key).

Step E extends Step C's TEXT-only template to also support INFOGRAPHIC
(number emphasis) and MAP (location card) — per the Phase 2 plan's own
note that these are "added when those scene types are actually
exercised," which is now. Every template also renders the scene's
`visual_concept.elements` as an icon chain (e.g. farmer -> government
building -> rupee), giving each still frame a sense of progression on
its own, before any Ken-Burns motion is applied at video-composition
time (rendering/adapters/ffmpeg_video_renderer.py).

If Chromium/Playwright fails to launch, PilSceneRenderer (registered as
a lower-priority renderer for the same SceneTypes) renders the same
scene content without a browser — see
core.interfaces.scene_renderer.SceneRendererRegistry.render_with_fallback.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from core.interfaces.scene_renderer import SceneRenderer
from core.models.enums import GenerationStatus, MediaAssetType, SceneType
from core.models.media import MediaAsset
from core.models.storyboard import Scene

logger = logging.getLogger("vaanireach.rendering.html_scene_renderer")

SCENE_IMAGE_DIR = Path(os.environ.get("SCENE_IMAGE_OUTPUT_DIR", "./data/scenes"))

# Matches the avatar hook clip / MoviePyVideoRenderer's established
# vertical short-form resolution (rendering/adapters/moviepy_video_renderer.py)
# so scenes concatenate without letterboxing.
VIEWPORT_WIDTH = 720
VIEWPORT_HEIGHT = 1280

# A small slice of .claude/skills/design-system's primitive tokens —
# just enough for a legible card, not a full design system.
_BG_COLOR = "#111827"  # gray-900
_FG_COLOR = "#F9FAFB"  # gray-50
_ACCENT_COLOR = "#3B82F6"  # blue-500

# Local, dependency-free "icons": each visual_concept.elements entry maps
# to a single glyph, rendered as an icon chain (no external image/icon
# library — zero new dependencies, per the plan's constraints).
_ELEMENT_GLYPHS: dict[str, str] = {
    "farmer_icon": "🧑‍🌾",
    "govt_building_icon": "🏛️",
    "rupee_badge": "₹",
    "checklist_icon": "📋",
    "checkmark_icon": "✅",
    "document_icon": "📄",
    "csc_building_icon": "🏢",
    "phone_icon": "📞",
    "calendar_icon": "📅",
    "countdown_marker": "⏳",
    "clock_icon": "⏰",
    "pulse_ring": "〰️",
    "megaphone_icon": "📢",
    "scheme_banner": "📜",
    "arrow_icon": "➡️",
    "url_badge": "🔗",
    "location_pin": "📍",
    "org_seal_icon": "🏛️",
    "community_silhouette": "👥",
    "question_mark_icon": "❓",
}

_AMOUNT_RE = re.compile(r"₹[\d,]+")
_DATE_RE = re.compile(r"\b\d{1,2} [A-Z][a-z]+ \d{4}\b")


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _icon_chain_html(elements: list[str]) -> str:
    glyphs = [_ELEMENT_GLYPHS.get(e, "•") for e in elements]
    return '<span class="chain-arrow">→</span>'.join(f'<span class="chain-icon">{g}</span>' for g in glyphs)


def _extract_emphasis(narration: str) -> str | None:
    """Number emphasis (INFOGRAPHIC scenes): pulls the first ₹-amount or
    absolute date literally out of the narration for a large styled
    treatment — never invents a value, only re-displays one already in
    the narration text (which itself only ever contains verified fact
    values)."""
    match = _AMOUNT_RE.search(narration) or _DATE_RE.search(narration)
    return match.group(0) if match else None


_BASE_STYLE = """
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{
    width: {width}px; height: {height}px;
    background: {bg}; color: {fg};
    font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    display: flex; align-items: center; justify-content: center;
  }}
  .card {{
    width: 100%; padding: 64px 48px;
    display: flex; flex-direction: column; align-items: center; gap: 32px;
  }}
  .text, .location-label {{ overflow-wrap: break-word; word-break: break-word; max-width: 100%; }}
  .accent-bar {{
    width: 64px; height: 6px; border-radius: 9999px; background: {accent};
  }}
  .chain {{ font-size: 56px; display: flex; align-items: center; gap: 12px; }}
  .chain-arrow {{ font-size: 32px; color: {accent}; opacity: 0.8; }}
  .text {{
    font-size: 40px; font-weight: 600; line-height: 1.375; text-align: center;
  }}
  .emphasis {{
    font-size: 96px; font-weight: 800; color: {accent}; line-height: 1;
  }}
  .pin {{ font-size: 120px; line-height: 1; }}
  .location-label {{
    font-size: 44px; font-weight: 700; text-align: center;
  }}
"""

_TEXT_SCENE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><style>{style}</style></head>
<body>
  <div class="card">
    <div class="accent-bar"></div>
    <div class="chain">{chain}</div>
    <div class="text">{text}</div>
  </div>
</body></html>"""

_INFOGRAPHIC_SCENE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><style>{style}</style></head>
<body>
  <div class="card">
    <div class="chain">{chain}</div>
    <div class="emphasis">{emphasis}</div>
    <div class="accent-bar"></div>
    <div class="text">{text}</div>
  </div>
</body></html>"""

_MAP_SCENE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><style>{style}</style></head>
<body>
  <div class="card">
    <div class="pin">📍</div>
    <div class="location-label">{text}</div>
    <div class="accent-bar"></div>
    <div class="chain">{chain}</div>
  </div>
</body></html>"""


def render_scene_html(scene: Scene) -> str:
    """Pure function: Scene -> the exact HTML/CSS string for its
    scene_type. Factored out of render_scene so it's independently
    testable (and inspectable) without needing Playwright at all."""
    style = _BASE_STYLE.format(
        width=VIEWPORT_WIDTH, height=VIEWPORT_HEIGHT, bg=_BG_COLOR, fg=_FG_COLOR, accent=_ACCENT_COLOR,
    )
    elements = scene.visual_concept.elements if scene.visual_concept else []
    chain = _icon_chain_html(elements)
    text = _escape_html(scene.narration_segment_text)

    if scene.scene_type == SceneType.INFOGRAPHIC:
        emphasis = _extract_emphasis(scene.narration_segment_text) or ""
        return _INFOGRAPHIC_SCENE_TEMPLATE.format(style=style, chain=chain, emphasis=_escape_html(emphasis), text=text)
    if scene.scene_type == SceneType.MAP:
        return _MAP_SCENE_TEMPLATE.format(style=style, chain=chain, text=text)
    return _TEXT_SCENE_TEMPLATE.format(style=style, chain=chain, text=text)


class HtmlSceneRenderer(SceneRenderer):
    def __init__(self, chromium_executable_path: str | None = None) -> None:
        """`chromium_executable_path` exists purely so tests can point at
        a nonexistent binary to deterministically simulate "Playwright is
        unavailable" without needing to actually break the real install."""
        self._chromium_executable_path = chromium_executable_path

    def supports(self, scene_type: SceneType) -> bool:
        return scene_type in (SceneType.TEXT, SceneType.INFOGRAPHIC, SceneType.MAP)

    def render_scene(self, scene: Scene) -> MediaAsset:
        if not self.supports(scene.scene_type):
            raise ValueError(f"HtmlSceneRenderer does not support scene_type={scene.scene_type!r}")

        from playwright.sync_api import sync_playwright  # local import: keep Playwright off the hot path when unused

        html = render_scene_html(scene)

        SCENE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        asset = MediaAsset(
            project_id="",  # filled in by caller if/when this wires into the full pipeline
            scene_id=scene.id,
            asset_type=MediaAssetType.IMAGE,
            generation_status=GenerationStatus.IN_PROGRESS,
            prompt_used=scene.narration_segment_text,
        )
        out_path = SCENE_IMAGE_DIR / f"{asset.id}.png"

        try:
            with sync_playwright() as p:
                launch_kwargs = {}
                if self._chromium_executable_path is not None:
                    launch_kwargs["executable_path"] = self._chromium_executable_path
                browser = p.chromium.launch(**launch_kwargs)
                try:
                    page = browser.new_page(viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT})
                    page.set_content(html)
                    page.screenshot(path=str(out_path))
                finally:
                    browser.close()
        except Exception as exc:
            logger.warning("render_scene: Playwright/Chromium failed (%s) — no PNG produced", exc)
            asset.generation_status = GenerationStatus.FAILED
            raise

        asset.storage_path = str(out_path)
        asset.provider_name = "html-playwright"
        asset.generation_status = GenerationStatus.COMPLETE
        return asset
