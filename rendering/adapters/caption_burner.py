"""caption_burner — burns per-scene, per-language captions into a
transparent alpha video track, composited onto the final video by
FfmpegVideoRenderer.compose_pip_and_captions (rendering/adapters/
ffmpeg_video_renderer.py).

Deliberately does NOT use ffmpeg's `subtitles` (libass) or `drawtext`
filters: this repo's ffmpeg build has neither --enable-libass nor
--enable-libfreetype (verified: `ffmpeg -filters` lists no `subtitles`
filter at all), so text is rendered client-side via Pillow instead —
the same "don't require an unverified system font/library prerequisite"
reasoning rendering/adapters/moviepy_video_renderer.py's own caption bar
already documents (see ADR-005). Each scene's caption frame is encoded
as a short qtrle (lossless, alpha-capable) clip and concatenated into
one track spanning the whole narration; ffmpeg's `overlay` filter
composites it directly (verified: qtrle + yuva420p encode and
overlay-composite correctly on this build).
"""
from __future__ import annotations

import logging
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from core.models.enums import LanguageCode
from core.models.storyboard import Scene

logger = logging.getLogger("vaanireach.rendering.caption_burner")

FONT_DIR = Path(__file__).resolve().parent.parent.parent / "fallback_assets" / "fonts"
_LATIN_FONT = FONT_DIR / "NotoSans-Regular.ttf"
_DEVANAGARI_FONT = FONT_DIR / "NotoSansDevanagari-Regular.ttf"

# Only Latin (en) and Devanagari (hi, mr) are bundled/tested end-to-end -
# every other LanguageCode falls back to the Latin font with a logged
# warning rather than failing outright: a caption that renders as
# missing-glyph boxes for an untested script is a visible, fixable
# defect, not a reason to refuse producing a video at all.
_FONT_FOR_LANGUAGE: dict[LanguageCode, Path] = {
    LanguageCode.EN: _LATIN_FONT,
    LanguageCode.HI: _DEVANAGARI_FONT,
    LanguageCode.MR: _DEVANAGARI_FONT,
}

CAPTION_BAR_HEIGHT = 180
CAPTION_FONT_SIZE = 34
CAPTION_MAX_CHARS_PER_LINE = 36
CAPTION_MAX_LINES = 3
_CAPTION_BG_RGBA = (0, 0, 0, 165)
_CAPTION_FG_RGBA = (255, 255, 255, 255)

_CUE_ENCODE_TIMEOUT_SECONDS = 30
_CONCAT_TIMEOUT_SECONDS = 60


def font_for_language(language: LanguageCode) -> Path:
    path = _FONT_FOR_LANGUAGE.get(language)
    if path is None:
        logger.warning(
            "font_for_language: no bundled font mapped for %s - falling back to Latin (Noto Sans); "
            "captions in this language may render as missing-glyph boxes", language.value,
        )
        return _LATIN_FONT
    return path


def render_caption_frame(text: str, *, width: int, font_path: Path) -> Image.Image:
    """One transparent width x CAPTION_BAR_HEIGHT RGBA frame: a
    semi-opaque dark bar spanning the full frame with centered, wrapped
    white text. Mirrors rendering/adapters/moviepy_video_renderer.py's
    _build_caption_clip, but returns a PIL.Image (this module composites
    via ffmpeg, not moviepy)."""
    wrapped_lines = textwrap.wrap(text.strip(), width=CAPTION_MAX_CHARS_PER_LINE) or [""]
    if len(wrapped_lines) > CAPTION_MAX_LINES:
        wrapped_lines = wrapped_lines[:CAPTION_MAX_LINES]
        wrapped_lines[-1] = wrapped_lines[-1].rstrip() + "…"
    wrapped_text = "\n".join(wrapped_lines)

    img = Image.new("RGBA", (width, CAPTION_BAR_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width, CAPTION_BAR_HEIGHT], fill=_CAPTION_BG_RGBA)

    font = ImageFont.truetype(str(font_path), CAPTION_FONT_SIZE)
    bbox = draw.multiline_textbbox((0, 0), wrapped_text, font=font, spacing=8, align="center")
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = max(0, (width - text_w) // 2)
    y = max(0, (CAPTION_BAR_HEIGHT - text_h) // 2)
    draw.multiline_text((x, y), wrapped_text, font=font, fill=_CAPTION_FG_RGBA, spacing=8, align="center")
    return img


def _render_cue_clip(scene: Scene, *, index: int, language: LanguageCode, width: int, height: int, tmp_dir: Path) -> Path:
    font_path = font_for_language(language)
    bar = render_caption_frame(scene.narration_segment_text, width=width, font_path=font_path)
    frame = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    frame.paste(bar, (0, height - CAPTION_BAR_HEIGHT), bar)
    frame_path = tmp_dir / f"caption_frame_{index}.png"
    frame.save(frame_path)

    cue_path = tmp_dir / f"caption_cue_{index}.mov"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-loop", "1", "-i", str(frame_path),
        "-t", f"{scene.duration_seconds:.3f}",
        "-pix_fmt", "yuva420p", "-c:v", "qtrle",
        str(cue_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=_CUE_ENCODE_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(f"caption_burner: cue {index} render failed: {result.stderr}")
    return cue_path


def build_caption_track(
    scenes: list[Scene], *, language: LanguageCode, width: int, height: int, tmp_dir: Path,
) -> Path:
    """Builds one full-frame (width x height), mostly-transparent alpha
    video covering the whole narration: each scene's own
    duration_seconds (real, TTS-measured, matching
    FfmpegVideoRenderer.compose_multi_scene's own timeline exactly) gets
    one caption frame, positioned in the bottom CAPTION_BAR_HEIGHT
    pixels. Returns the path to a qtrle-encoded .mov with an alpha
    channel, ready for ffmpeg's `overlay` filter."""
    if not scenes:
        raise ValueError("build_caption_track: scenes is empty")

    cue_paths = [
        _render_cue_clip(scene, index=i, language=language, width=width, height=height, tmp_dir=tmp_dir)
        for i, scene in enumerate(scenes)
    ]
    if len(cue_paths) == 1:
        return cue_paths[0]

    track_path = tmp_dir / "caption_track.mov"
    inputs: list[str] = []
    for p in cue_paths:
        inputs += ["-i", str(p)]
    concat_inputs = "".join(f"[{i}:v]" for i in range(len(cue_paths)))
    filter_complex = f"{concat_inputs}concat=n={len(cue_paths)}:v=1:a=0[vout]"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-pix_fmt", "yuva420p", "-c:v", "qtrle",
        str(track_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=_CONCAT_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(f"caption_burner: track concat failed: {result.stderr}")
    return track_path
