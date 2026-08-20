"""caption_burner — short, timed caption cues (never the whole script in
one block) burned into a transparent alpha video track, composited onto
the final video by FfmpegVideoRenderer.compose_final_video (rendering/
adapters/ffmpeg_video_renderer.py).

Ported from main branch's rendering/adapters/caption_burner.py (per-Scene
captions for the TemplateStoryDirector pipeline) and generalized to a
flat list of (cue_text, duration_seconds) tuples instead of Scene objects
— this dashboard's pipeline (dashboard/app.py) has ONE narration script
per language, not main's per-fact-role Scene breakdown, so
`split_narration_into_cues()` below does the segmenting main gets for
free from its Scene list.

Also adds a third bundled font (Noto Sans Bengali) — main only ships
Latin + Devanagari (en/hi/mr); this dashboard's three default target
languages are Hindi, Marathi, and Bengali (see dashboard/app.py's
TARGET_LANGUAGES), so Bengali needed its own glyph coverage, not a
missing-glyph-box fallback to Latin.

Renders via Pillow rather than ffmpeg's `subtitles`/`drawtext` filters —
kept even though this environment's ffmpeg build DOES have
--enable-libass/--enable-libfreetype (unlike the build main's docstring
was written against), because Pillow gives precise, testable control
over the 2-line cap, the gradient card, and per-language font selection
without depending on a specific ffmpeg build's filter support.
"""
from __future__ import annotations

import logging
import re
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from core.models.enums import LanguageCode

logger = logging.getLogger("vaanireach.rendering.caption_burner")

FONT_DIR = Path(__file__).resolve().parent.parent.parent / "fallback_assets" / "fonts"
_LATIN_FONT = FONT_DIR / "NotoSans-Regular.ttf"
_DEVANAGARI_FONT = FONT_DIR / "NotoSansDevanagari-Regular.ttf"
_BENGALI_FONT = FONT_DIR / "NotoSansBengali-Regular.ttf"

# Every LanguageCode this dashboard actually offers (dashboard/app.py's
# TARGET_LANGUAGES) has a real bundled font; anything else falls back to
# Latin with a logged warning rather than failing outright — a caption
# rendering as missing-glyph boxes is a visible, fixable defect, not a
# reason to refuse producing a video.
_FONT_FOR_LANGUAGE: dict[LanguageCode, Path] = {
    LanguageCode.EN: _LATIN_FONT,
    LanguageCode.HI: _DEVANAGARI_FONT,
    LanguageCode.MR: _DEVANAGARI_FONT,
    LanguageCode.BN: _BENGALI_FONT,
}

CAPTION_BAR_HEIGHT = 200
CAPTION_FONT_SIZE = 34
CAPTION_MAX_CHARS_PER_LINE = 34
CAPTION_MAX_LINES = 2  # hard cap: short, timed cues, never a wall of text on screen
_CAPTION_FG_RGBA = (255, 255, 255, 255)
_GRADIENT_TOP_ALPHA = 0  # fully transparent at the top of the bar
_GRADIENT_BOTTOM_ALPHA = 210  # near-opaque black at the very bottom — the "safe zone" card

_CUE_ENCODE_TIMEOUT_SECONDS = 30
_CONCAT_TIMEOUT_SECONDS = 60

# Sentence-ending punctuation across Latin + Devanagari (both Hindi and
# Marathi) + Bengali scripts — Devanagari's danda (।) is also the
# standard sentence-end mark in Bengali, so one pattern covers all three
# bundled languages.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?।])\s+")
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[,;:])\s+")
MAX_WORDS_PER_CUE = 9  # short enough to reliably wrap to <= CAPTION_MAX_LINES at CAPTION_MAX_CHARS_PER_LINE
MIN_CUE_SECONDS = 0.6


def font_for_language(language: LanguageCode) -> Path:
    path = _FONT_FOR_LANGUAGE.get(language)
    if path is None:
        logger.warning(
            "font_for_language: no bundled font mapped for %s - falling back to Latin (Noto Sans); "
            "captions in this language may render as missing-glyph boxes", language.value,
        )
        return _LATIN_FONT
    return path


# --------------------------------------------------------------------------- narration -> timed cues

def _split_into_word_chunks(words: list[str], *, max_words: int) -> list[list[str]]:
    return [words[i : i + max_words] for i in range(0, len(words), max_words)] or [[]]


def split_narration_into_cues(text: str, total_duration: float) -> list[tuple[str, float]]:
    """Splits one narration script into short cues (<= MAX_WORDS_PER_CUE
    words each, reliably wrapping to <= CAPTION_MAX_LINES lines), each
    assigned a slice of `total_duration` proportional to its own word
    count — the closest approximation to real per-word timing available
    without a forced-aligner/ASR step in this pipeline (the same
    "audio duration is the source of truth for timing" principle the
    rest of this renderer already follows, applied at cue granularity).

    Splits on sentence boundaries first, then clause boundaries
    (comma/semicolon/colon), then hard word-count chunks for anything
    still too long — so a cue is never a mid-clause fragment unless the
    clause itself is genuinely long."""
    text = text.strip()
    if not text:
        return [("", max(total_duration, MIN_CUE_SECONDS))]

    cues: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence.split()) <= MAX_WORDS_PER_CUE:
            cues.append(sentence)
            continue
        for clause in _CLAUSE_SPLIT_RE.split(sentence):
            clause = clause.strip()
            if not clause:
                continue
            if len(clause.split()) <= MAX_WORDS_PER_CUE:
                cues.append(clause)
            else:
                cues.extend(" ".join(chunk) for chunk in _split_into_word_chunks(clause.split(), max_words=MAX_WORDS_PER_CUE))

    if not cues:
        cues = [text]

    word_counts = [max(1, len(c.split())) for c in cues]
    total_words = sum(word_counts)

    durations = [total_duration * wc / total_words for wc in word_counts]
    # Enforce a sensible floor per cue (a 1-word cue shouldn't flash for
    # 0.1s) by borrowing proportionally from the longer cues, then let
    # the last cue absorb any float-rounding remainder so the sum stays
    # exactly total_duration.
    durations = [max(d, MIN_CUE_SECONDS) for d in durations]
    scale = total_duration / sum(durations) if sum(durations) > 0 else 1.0
    durations = [d * scale for d in durations]
    if durations:
        durations[-1] += total_duration - sum(durations)

    return list(zip(cues, durations))


# --------------------------------------------------------------------------- rendering

def render_caption_frame(text: str, *, width: int, font_path: Path) -> Image.Image:
    """One transparent width x CAPTION_BAR_HEIGHT RGBA frame: a dark
    bottom-anchored gradient card (transparent at the top, near-opaque at
    the very bottom "safe zone") with centered, wrapped white text over
    it — high-contrast against any B-roll image, including one with its
    own busy detail or (occasionally) legible-looking text in it."""
    wrapped_lines = textwrap.wrap(text.strip(), width=CAPTION_MAX_CHARS_PER_LINE) or [""]
    if len(wrapped_lines) > CAPTION_MAX_LINES:
        wrapped_lines = wrapped_lines[:CAPTION_MAX_LINES]
        wrapped_lines[-1] = wrapped_lines[-1].rstrip() + "…"
    wrapped_text = "\n".join(wrapped_lines)

    img = Image.new("RGBA", (width, CAPTION_BAR_HEIGHT), (0, 0, 0, 0))
    gradient_row = Image.new("L", (1, CAPTION_BAR_HEIGHT))
    for y in range(CAPTION_BAR_HEIGHT):
        t = y / max(1, CAPTION_BAR_HEIGHT - 1)
        gradient_row.putpixel((0, y), int(_GRADIENT_TOP_ALPHA + t * (_GRADIENT_BOTTOM_ALPHA - _GRADIENT_TOP_ALPHA)))
    alpha = gradient_row.resize((width, CAPTION_BAR_HEIGHT))
    img.paste(Image.new("RGBA", (width, CAPTION_BAR_HEIGHT), (0, 0, 0, 255)), (0, 0), alpha)

    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(font_path), CAPTION_FONT_SIZE)
    bbox = draw.multiline_textbbox((0, 0), wrapped_text, font=font, spacing=10, align="center")
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = max(0, (width - text_w) // 2)
    # Weighted toward the bottom of the bar (the "safe zone"), not
    # vertically centered in the whole gradient — reads as anchored to
    # the frame edge rather than floating mid-bar.
    y = max(0, CAPTION_BAR_HEIGHT - text_h - 28)
    draw.multiline_text((x, y), wrapped_text, font=font, fill=_CAPTION_FG_RGBA, spacing=10, align="center")
    return img


def _render_cue_clip(
    text: str, duration: float, *, index: int, font_path: Path, width: int, height: int, tmp_dir: Path,
) -> Path:
    bar = render_caption_frame(text, width=width, font_path=font_path)
    frame = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    frame.paste(bar, (0, height - CAPTION_BAR_HEIGHT), bar)
    frame_path = tmp_dir / f"caption_frame_{index}.png"
    frame.save(frame_path)

    cue_path = tmp_dir / f"caption_cue_{index}.mov"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-loop", "1", "-i", str(frame_path),
        "-t", f"{max(duration, MIN_CUE_SECONDS):.3f}",
        "-pix_fmt", "yuva420p", "-c:v", "qtrle",
        str(cue_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=_CUE_ENCODE_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(f"caption_burner: cue {index} render failed: {result.stderr}")
    return cue_path


def build_caption_track(
    cues: list[tuple[str, float]], *, language: LanguageCode, width: int, height: int, tmp_dir: Path,
) -> Path:
    """Builds one full-frame (width x height), mostly-transparent alpha
    video spanning the whole timeline `cues` covers: each (text,
    duration) cue gets one caption frame, positioned in the bottom
    CAPTION_BAR_HEIGHT pixels. Returns the path to a qtrle-encoded .mov
    with an alpha channel, ready for ffmpeg's `overlay` filter."""
    if not cues:
        raise ValueError("build_caption_track: cues is empty")

    font_path = font_for_language(language)
    cue_paths = [
        _render_cue_clip(text, duration, index=i, font_path=font_path, width=width, height=height, tmp_dir=tmp_dir)
        for i, (text, duration) in enumerate(cues)
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
