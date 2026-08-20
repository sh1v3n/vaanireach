"""PilSceneRenderer — the fallback SceneRenderer for TEXT/INFOGRAPHIC/MAP
scenes (Phase 2 media plan). Renders the same scene content as a styled
card directly with Pillow (solid background + wrapped text, no browser)
so the pipeline still produces a scene image if Chromium/Playwright
fails to launch.

Icon chain elements are rendered as bracketed text labels (e.g.
"[farmer_icon] -> [rupee_badge]") rather than emoji glyphs — Pillow's
default font has no reliable color-emoji support across environments,
and this is the fallback path: it should never depend on font
availability it can't guarantee. HtmlSceneRenderer's chain (real emoji,
via the system browser's font stack) is the richer primary presentation.

Registered in SceneRendererRegistry as a lower-priority renderer for the
same SceneTypes as HtmlSceneRenderer — see
core.interfaces.scene_renderer.SceneRendererRegistry.render_with_fallback,
which tries renderers in registration order and only falls through to
this one if the primary raises.
"""
from __future__ import annotations

import logging
import os
import re
import textwrap
from pathlib import Path

from core.interfaces.scene_renderer import SceneRenderer
from core.models.enums import GenerationStatus, MediaAssetType, SceneType
from core.models.media import MediaAsset
from core.models.storyboard import Scene

logger = logging.getLogger("vaanireach.rendering.pil_scene_renderer")

SCENE_IMAGE_DIR = Path(os.environ.get("SCENE_IMAGE_OUTPUT_DIR", "./data/scenes"))

# Same canvas size as HtmlSceneRenderer so scenes from either renderer
# concatenate without letterboxing.
CANVAS_WIDTH = 720
CANVAS_HEIGHT = 1280

_BG_COLOR = (17, 24, 39)  # gray-900, matches HtmlSceneRenderer's --color-gray-900
_FG_COLOR = (249, 250, 251)  # gray-50
_ACCENT_COLOR = (59, 130, 246)  # blue-500

_AMOUNT_RE = re.compile(r"₹[\d,]+")
_DATE_RE = re.compile(r"\b\d{1,2} [A-Z][a-z]+ \d{4}\b")


def _extract_emphasis(narration: str) -> str | None:
    match = _AMOUNT_RE.search(narration) or _DATE_RE.search(narration)
    return match.group(0) if match else None


def _chain_label(elements: list[str]) -> str:
    return "  ->  ".join(f"[{e}]" for e in elements)


class PilSceneRenderer(SceneRenderer):
    def supports(self, scene_type: SceneType) -> bool:
        return scene_type in (SceneType.TEXT, SceneType.INFOGRAPHIC, SceneType.MAP)

    def render_scene(self, scene: Scene) -> MediaAsset:
        if not self.supports(scene.scene_type):
            raise ValueError(f"PilSceneRenderer does not support scene_type={scene.scene_type!r}")

        from PIL import Image, ImageDraw, ImageFont  # local import: keep Pillow off the hot path when unused

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
            img = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), color=_BG_COLOR)
            draw = ImageDraw.Draw(img)
            y = 140

            elements = scene.visual_concept.elements if scene.visual_concept else []
            if elements:
                chain_text = textwrap.fill(_chain_label(elements), width=26)
                bbox = draw.multiline_textbbox((0, 0), chain_text, spacing=8)
                w = bbox[2] - bbox[0]
                draw.multiline_text(((CANVAS_WIDTH - w) // 2, y), chain_text, fill=_ACCENT_COLOR, spacing=8, align="center")
                y += (bbox[3] - bbox[1]) + 48

            if scene.scene_type == SceneType.INFOGRAPHIC:
                emphasis = _extract_emphasis(scene.narration_segment_text)
                if emphasis:
                    try:
                        big_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 96)
                    except OSError:
                        big_font = ImageFont.load_default()
                    bbox = draw.textbbox((0, 0), emphasis, font=big_font)
                    w = bbox[2] - bbox[0]
                    draw.text(((CANVAS_WIDTH - w) // 2, y), emphasis, fill=_ACCENT_COLOR, font=big_font)
                    y += (bbox[3] - bbox[1]) + 48
            elif scene.scene_type == SceneType.MAP:
                pin_text = "[location_pin]"
                bbox = draw.textbbox((0, 0), pin_text)
                w = bbox[2] - bbox[0]
                draw.text(((CANVAS_WIDTH - w) // 2, y), pin_text, fill=_FG_COLOR)
                y += (bbox[3] - bbox[1]) + 32

            # accent bar
            bar_w, bar_h = 64, 6
            draw.rounded_rectangle(
                [(CANVAS_WIDTH - bar_w) // 2, y, (CANVAS_WIDTH + bar_w) // 2, y + bar_h],
                radius=3, fill=_ACCENT_COLOR,
            )
            y += bar_h + 32

            wrapped = textwrap.fill(scene.narration_segment_text, width=24)
            text_bbox = draw.multiline_textbbox((0, 0), wrapped, spacing=14)
            text_w = text_bbox[2] - text_bbox[0]
            draw.multiline_text(
                (max(0, (CANVAS_WIDTH - text_w) // 2), y), wrapped, fill=_FG_COLOR, spacing=14, align="center"
            )

            img.save(out_path, format="PNG")
        except Exception as exc:
            logger.warning("render_scene: Pillow rendering failed (%s) — no PNG produced", exc)
            asset.generation_status = GenerationStatus.FAILED
            raise

        asset.storage_path = str(out_path)
        asset.provider_name = "pillow-fallback"
        asset.generation_status = GenerationStatus.COMPLETE
        return asset
