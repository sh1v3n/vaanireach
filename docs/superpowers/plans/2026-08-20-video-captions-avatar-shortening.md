# Burned-in Captions, Avatar PiP Lip-Sync, 20-30s Video Length — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the template multilingual pipeline (`tests/demo_multilingual_video.py`'s path) produces a
20-30s video with burned-in per-language captions and a persistent lip-synced presenter avatar in a
bottom-left PiP box — a professional explainer video with a presenter, not a slideshow-with-voiceover.

**Architecture:** existing `compose_multi_scene()` B-roll output is composited with two new layers
in one final ffmpeg pass — a looping avatar PiP overlay (avatar generated once per language via the
existing `AvatarFailoverProvider`, lip-synced to the full concatenated narration audio) and a
Pillow-rendered caption track (burned in as a bottom bar). `TemplateStoryDirector` drops two
pure-restatement scenes to bring runtime from ~47s toward 20-30s.

**Tech Stack:** Python 3.12, ffmpeg 9.0.1 (subprocess, no libass/libfreetype — verified during
planning; see Task 3), Pillow, pytest.

**Spec:** `docs/superpowers/specs/2026-08-20-video-captions-avatar-shortening-design.md`

## Global Constraints

- The avatar is REQUIRED MVP scope. Every run always attempts full avatar generation + PiP
  compositing + caption burn-in. The fallback to plain captioned-B-roll (Task 7) triggers only on a
  genuine runtime failure and must log loudly — never treat it as an accepted steady state, and
  never write code that skips/defers avatar generation by default.
- Avatar placement: PiP rectangle, bottom-left corner, full video duration, looping if the clip is
  shorter than the video. Photorealistic presenter portrait (existing `AVATAR_IMAGE_PROMPT` style).
  One narration audio track — the avatar lip-syncs to the exact same audio the B-roll plays.
- Captions: burned-in full-width bottom bar, above the PiP box. Sidecar `.srt`/`.vtt` generation is
  unchanged and kept alongside the burn-in.
- Duration: `TARGET_DURATION_MIN_SECONDS = 20.0`, `TARGET_DURATION_MAX_SECONDS = 30.0`. Best-effort,
  not a hard cap — verified sample-document result after this plan: ~22.6s (pre-TTS estimate).
- Scope is the template pipeline only (`providers/narrative/template_story_director.py` →
  `rendering/multilingual_video.py` → `rendering/adapters/ffmpeg_video_renderer.py`). The separate
  dashboard/MoviePy pipeline is untouched.
- Bundled font coverage is Latin + Devanagari only (covers en/hi/mr, the tested language set). Other
  `LanguageCode` values fall back to the Latin font with a logged warning — not a silent failure,
  not a hard error.

---

## File Structure

**New files:**
- `fallback_assets/fonts/NotoSans-Regular.ttf`, `fallback_assets/fonts/NotoSansDevanagari-Regular.ttf`
  — bundled OFL-licensed fonts for caption text rendering (Task 2).
- `rendering/adapters/caption_burner.py` — Pillow-based per-scene caption frame rendering + alpha
  video track assembly (Task 3).
- `providers/video/avatar_portrait.py` — shared, cached presenter-portrait image helper (Task 6).

**Modified files:**
- `providers/narrative/template_story_director.py` — drop CTA/CLOSING scenes, update duration
  constants (Task 1).
- `rendering/adapters/ffmpeg_video_renderer.py` — extract `concat_audio_files()` (Task 4), add
  `compose_pip_and_captions()` (Task 5).
- `rendering/multilingual_video.py` — wire avatar + PiP + caption compositing into
  `generate_language_video()` with a try/except fallback (Task 7).
- `tests/demo_multilingual_video.py` — report avatar/caption compositing status (Task 8).
- `tests/test_narrative_story_director.py` — update duration assertion, add CTA/CLOSING-absence
  regression test (Task 1).

---

### Task 1: Trim TemplateStoryDirector to 20-30s (drop CTA/CLOSING scenes)

**Files:**
- Modify: `providers/narrative/template_story_director.py:45-46,292-316`
- Test: `tests/test_narrative_story_director.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `TemplateStoryDirector.plan_narrative_arc()` no longer emits `NarrativeRole.CTA` or
  `NarrativeRole.CLOSING` scenes. `TARGET_DURATION_MIN_SECONDS = 20.0`,
  `TARGET_DURATION_MAX_SECONDS = 30.0` (unchanged consumers — nothing in the codebase reads these
  two constants today, confirmed by repo-wide grep during planning).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_narrative_story_director.py` (near `test_scene_count_is_not_hardcoded`):

```python
def test_cta_and_closing_scenes_are_never_produced(arc_and_scenes):
    """Regression guard: CTA and CLOSING are pure restatements of facts
    already spoken earlier (CTA repeats HOW_TO's URL + DEADLINE's date;
    CLOSING repeats ANNOUNCEMENT's scheme name + HOOK's org) — dropped
    entirely so the video can fit a 20-30s target without losing any
    fact. See docs/superpowers/specs/2026-08-20-video-captions-avatar-shortening-design.md."""
    _, scenes, _ = arc_and_scenes
    roles = {s.narrative_role for s in scenes}
    assert NarrativeRole.CTA not in roles
    assert NarrativeRole.CLOSING not in roles
```

Update the existing `test_target_duration_respected` (same file) — change the hardcoded range:

```python
def test_target_duration_respected(arc_and_scenes):
    arc, scenes, _ = arc_and_scenes
    assert 20.0 <= arc.target_duration_seconds <= 30.0, (
        f"target_duration_seconds={arc.target_duration_seconds} outside the 20-30s range"
    )
    scene_sum = sum(s.duration_seconds for s in scenes)
    assert scene_sum == pytest.approx(arc.target_duration_seconds, abs=0.01)
```

Also update the module docstring's item 8 (`"Target duration is respected: 30-45s range..."` near
the top of the file) to say `20-30s range` instead — keep the docstring's requirements list honest.

Check whether `NarrativeRole` is already imported in `tests/test_narrative_story_director.py` (it
uses `NarrativeRole` elsewhere in the file already via `core.models.enums` — confirm the import line
includes it; add `NarrativeRole` to the existing `from core.models.enums import ...` line if it's
missing).

- [ ] **Step 2: Run tests to verify the new/changed ones fail**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_narrative_story_director.py -v`
Expected: `test_cta_and_closing_scenes_are_never_produced` FAILS (CTA/CLOSING are still produced
today); `test_target_duration_respected` FAILS (current value ~33.0s is outside 20-30s).

- [ ] **Step 3: Remove the CTA scene-generation block**

In `providers/narrative/template_story_director.py`, delete lines 292-305 in full (the comment plus
the entire `if how_to_facts or deadline_facts:` block that calls
`add_scene(NarrativeRole.CTA, ...)`):

```python
# --- CTA: restates HOW_TO's preferred fact (URL if present) + DEADLINE.
# No relative-temporal language ("today", "now") — only the
# DEADLINE fact's own absolute date, per the fact-consistency rule.
how_to_facts = buckets.get(NarrativeRole.HOW_TO, [])
if how_to_facts or deadline_facts:
    cta_facts = how_to_facts + deadline_facts
    how_to_anchor = next((f for f in how_to_facts if f.fact_type == FactType.URL),
                          how_to_facts[0] if how_to_facts else None)
    parts = ["Submit your application"]
    if deadline_facts:
        parts.append(f"before {_join_values(deadline_facts)}")
    if how_to_anchor is not None:
        parts.append(f"at {how_to_anchor.value}")
    add_scene(NarrativeRole.CTA, cta_facts, " ".join(parts) + ".")
```

- [ ] **Step 4: Remove only the CLOSING scene-generation part (keep `announcement_facts`)**

`announcement_facts` (assigned in this same block) is reused later at
`title = announcement_facts[0].value if announcement_facts else "Untitled Scheme"` — that line must
keep working, so only remove the CLOSING-specific lines, not the whole block. Replace:

```python
        # --- CLOSING: restates ANNOUNCEMENT + CONTEXT, always present if either exists ---
        announcement_facts = buckets.get(NarrativeRole.ANNOUNCEMENT, [])
        closing_facts = announcement_facts + context_facts
        if closing_facts:
            summary_bits = []
            if announcement_facts:
                summary_bits.append(_join_values(announcement_facts))
            if context_facts:
                summary_bits.append(f"from {_join_values(context_facts)}")
            add_scene(NarrativeRole.CLOSING, closing_facts, " — ".join(summary_bits) + ".")
```

with:

```python
        # announcement_facts is kept (not folded into a CLOSING scene — see
        # docs/superpowers/specs/2026-08-20-video-captions-avatar-shortening-design.md) because
        # `title` below still needs it.
        announcement_facts = buckets.get(NarrativeRole.ANNOUNCEMENT, [])
```

- [ ] **Step 5: Update the duration constants**

```python
TARGET_DURATION_MIN_SECONDS = 20.0
TARGET_DURATION_MAX_SECONDS = 30.0
```

(was `30.0` / `45.0` at lines 45-46).

- [ ] **Step 6: Run the full test file, verify everything passes**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_narrative_story_director.py -v`
Expected: all tests PASS, including the two from Step 1. `test_target_duration_respected`'s real
measured value for the sample document is ~22.6s (verified during planning — comfortably inside
20-30s).

- [ ] **Step 7: Run the broader test suite for regressions**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_cloudflare_scene_renderer.py tests/test_step_e_multi_scene_composition.py -v`
Expected: all PASS unchanged — `test_cloudflare_scene_renderer.py` constructs its own CLOSING-role
`Scene` directly (independent of `TemplateStoryDirector`), so it's unaffected by this change.

- [ ] **Step 8: Commit**

```bash
cd vaanireach
git add providers/narrative/template_story_director.py tests/test_narrative_story_director.py
git commit -m "Drop CTA/CLOSING restatement scenes, target 20-30s video length

Both scenes add no new facts (CTA repeats HOW_TO/DEADLINE, CLOSING
repeats ANNOUNCEMENT/HOOK) - removing them cuts ~10s off the sample
document's runtime with zero information loss, landing at ~22.6s
(previously ~33.0s pre-TTS-estimate / 47s measured real Marathi audio).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Bundle Noto fonts for caption rendering

**Files:**
- Create: `fallback_assets/fonts/NotoSans-Regular.ttf`
- Create: `fallback_assets/fonts/NotoSansDevanagari-Regular.ttf`
- Create: `fallback_assets/fonts/LICENSE.txt`

**Interfaces:**
- Produces: two on-disk font files at fixed, known paths — `caption_burner.py` (Task 3) loads them
  directly via `PIL.ImageFont.truetype(path, size)`, no system font search.

This machine's ffmpeg build has neither `--enable-libass` nor `--enable-libfreetype` (verified: run
`ffmpeg -filters 2>/dev/null | grep -i subtitle` — empty output, confirming the `subtitles` filter
doesn't exist; `ffmpeg -version | grep -o -- --enable-libass` — also empty). This is why captions in
this plan are rendered client-side with Pillow (Task 3) instead of ffmpeg's `subtitles`/`drawtext`
filters — the same "don't require an unverified system font/library prerequisite" reasoning
`rendering/adapters/moviepy_video_renderer.py` already documents for its own caption bar (see
ADR-005). Bundling the fonts (rather than searching system font paths) removes a real cross-platform
failure mode: a machine with no Devanagari font installed would otherwise silently render tofu boxes
for Hindi/Marathi captions.

- [ ] **Step 1: Download the fonts (verified working URLs, OFL-licensed)**

```bash
cd vaanireach
mkdir -p fallback_assets/fonts
curl -sL -o fallback_assets/fonts/NotoSans-Regular.ttf \
  "https://github.com/google/fonts/raw/main/ofl/notosans/NotoSans%5Bwdth%2Cwght%5D.ttf"
curl -sL -o fallback_assets/fonts/NotoSansDevanagari-Regular.ttf \
  "https://github.com/google/fonts/raw/main/ofl/notosansdevanagari/NotoSansDevanagari%5Bwdth%2Cwght%5D.ttf"
```

Expected sizes (verified during planning): `NotoSans-Regular.ttf` ≈ 2,049,096 bytes,
`NotoSansDevanagari-Regular.ttf` ≈ 647,144 bytes. Both are variable fonts (multiple weights in one
file); loading them with `PIL.ImageFont.truetype(path, size)` with no extra arguments uses the
default (Regular) instance, which is what's needed here.

- [ ] **Step 2: Verify both fonts load and render real glyphs**

```bash
cd vaanireach
.venv/bin/python3 -c "
from PIL import ImageFont
latin = ImageFont.truetype('fallback_assets/fonts/NotoSans-Regular.ttf', 32)
deva = ImageFont.truetype('fallback_assets/fonts/NotoSansDevanagari-Regular.ttf', 32)
assert latin.getmask('Hello').getbbox() is not None
assert deva.getmask('नमस्ते').getbbox() is not None
print('OK: both fonts load and render real glyphs')
"
```

Expected: prints `OK: both fonts load and render real glyphs` with no exception.

- [ ] **Step 3: Add a LICENSE.txt noting provenance**

```bash
cat > fallback_assets/fonts/LICENSE.txt << 'EOF'
Noto Sans and Noto Sans Devanagari are licensed under the SIL Open Font
License, Version 1.1 (https://openfontlicense.org/).

Source: https://github.com/google/fonts (ofl/notosans, ofl/notosansdevanagari)
Bundled here so caption burn-in (rendering/adapters/caption_burner.py) does
not depend on system font availability — see
docs/superpowers/specs/2026-08-20-video-captions-avatar-shortening-design.md.
EOF
```

- [ ] **Step 4: Commit**

```bash
cd vaanireach
git add fallback_assets/fonts/
git commit -m "Bundle Noto Sans + Noto Sans Devanagari for caption burn-in

This ffmpeg build has no libass/libfreetype (verified: no 'subtitles'
filter), so captions render client-side via Pillow instead - bundling
these OFL fonts means that rendering doesn't depend on whatever's
installed system-wide, removing a real cross-platform failure mode for
Hindi/Marathi captions.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `caption_burner.py` — per-scene caption rendering + alpha video track

**Files:**
- Create: `rendering/adapters/caption_burner.py`
- Test: `tests/test_caption_burner.py`

**Interfaces:**
- Consumes: `Scene` (from `core.models.storyboard`, has `.narration_segment_text` and
  `.duration_seconds`), `LanguageCode` (from `core.models.enums`), the bundled fonts from Task 2 at
  `fallback_assets/fonts/NotoSans-Regular.ttf` / `NotoSansDevanagari-Regular.ttf`.
- Produces: `font_for_language(language: LanguageCode) -> Path`,
  `render_caption_frame(text: str, *, width: int, font_path: Path) -> PIL.Image.Image` (RGBA,
  `width` x `CAPTION_BAR_HEIGHT`), `build_caption_track(scenes: list[Scene], *, language: LanguageCode, width: int, height: int, tmp_dir: Path) -> Path`
  (path to a `width` x `height` RGBA `.mov` file, qtrle-encoded, total duration = sum of
  `scene.duration_seconds`). `CAPTION_BAR_HEIGHT = 180` is a public constant — Task 5 needs it to
  position the avatar PiP box above the caption bar.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_caption_burner.py`:

```python
"""caption_burner: Pillow-rendered caption frames + a qtrle alpha video
track, composited by FfmpegVideoRenderer.compose_pip_and_captions
(Task 5) — see docs/superpowers/specs/2026-08-20-video-captions-avatar-shortening-design.md
for why this doesn't use ffmpeg's subtitles/drawtext filters."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core.models.enums import LanguageCode, NarrativeRole, SceneType, TransitionType  # noqa: E402
from core.models.storyboard import Scene  # noqa: E402
from rendering.adapters.caption_burner import (  # noqa: E402
    CAPTION_BAR_HEIGHT, build_caption_track, font_for_language, render_caption_frame,
)


def _scene(text: str, duration: float, order: int = 0) -> Scene:
    return Scene(
        storyboard_id="caption-test", order_index=order, scene_type=SceneType.TEXT,
        narrative_role=NarrativeRole.BENEFIT, narration_segment_text=text, duration_seconds=duration,
    )


def test_font_for_language_maps_hindi_and_marathi_to_devanagari():
    from rendering.adapters.caption_burner import _DEVANAGARI_FONT, _LATIN_FONT
    assert font_for_language(LanguageCode.HI) == _DEVANAGARI_FONT
    assert font_for_language(LanguageCode.MR) == _DEVANAGARI_FONT
    assert font_for_language(LanguageCode.EN) == _LATIN_FONT


def test_font_for_language_falls_back_to_latin_for_unbundled_scripts():
    from rendering.adapters.caption_burner import _LATIN_FONT
    assert font_for_language(LanguageCode.TA) == _LATIN_FONT  # Tamil not bundled - documented fallback


def test_render_caption_frame_is_transparent_except_the_bar():
    from rendering.adapters.caption_burner import _LATIN_FONT
    img = render_caption_frame("Hello world", width=720, font_path=_LATIN_FONT)
    assert img.size == (720, CAPTION_BAR_HEIGHT)
    assert img.mode == "RGBA"
    # top-left corner is inside the semi-opaque bar (bar fills the whole frame) - alpha must be > 0
    assert img.getpixel((0, 0))[3] > 0


def test_render_caption_frame_renders_devanagari_without_error():
    from rendering.adapters.caption_burner import _DEVANAGARI_FONT
    img = render_caption_frame("किसान सहायता योजना जाहीर करत आहोत", width=720, font_path=_DEVANAGARI_FONT)
    assert img.size == (720, CAPTION_BAR_HEIGHT)


def test_build_caption_track_duration_matches_sum_of_scene_durations(tmp_path):
    scenes = [_scene("First line of narration.", 2.0, 0), _scene("Second, slightly longer line here.", 3.0, 1)]
    track_path = build_caption_track(scenes, language=LanguageCode.EN, width=720, height=1280, tmp_dir=tmp_path)
    assert track_path.exists()

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(track_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert probe.returncode == 0, probe.stderr
    data = json.loads(probe.stdout)
    stream = data["streams"][0]
    assert stream["width"] == 720
    assert stream["height"] == 1280
    assert stream["pix_fmt"] == "yuva420p"
    actual_duration = float(data["format"]["duration"])
    assert actual_duration == pytest.approx(5.0, abs=0.2)  # 2.0 + 3.0, +/- frame-rate quantization


def test_build_caption_track_single_scene_skips_concat(tmp_path):
    """One scene needs no concat step - the single cue clip is used directly."""
    scenes = [_scene("Only one line.", 2.0, 0)]
    track_path = build_caption_track(scenes, language=LanguageCode.EN, width=720, height=1280, tmp_dir=tmp_path)
    assert track_path.exists()
```

- [ ] **Step 2: Run tests to verify they fail with ImportError**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_caption_burner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rendering.adapters.caption_burner'`.

- [ ] **Step 3: Write `rendering/adapters/caption_burner.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_caption_burner.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd vaanireach
git add rendering/adapters/caption_burner.py tests/test_caption_burner.py
git commit -m "Add caption_burner: Pillow-rendered caption bar + alpha video track

Renders per-scene captions client-side (Pillow, bundled Noto fonts)
rather than via ffmpeg's subtitles/drawtext filters, which this build's
ffmpeg lacks (no libass/libfreetype - verified during planning). Each
scene's caption frame is qtrle-encoded then concatenated into one alpha
track spanning the narration, ready for a later overlay compositing
step.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Extract `concat_audio_files()` as a reusable module function

**Files:**
- Modify: `rendering/adapters/ffmpeg_video_renderer.py:172-373` (the `FfmpegVideoRenderer` class —
  specifically the `_concat_audio` staticmethod around lines 332-372, and its call site inside
  `compose_multi_scene` around line 230)
- Test: `tests/test_ffmpeg_audio_concat_mismatched_formats.py` (existing — must keep passing),
  `tests/test_step_e_multi_scene_composition.py` (existing — must keep passing)

**Interfaces:**
- Produces: module-level `concat_audio_files(audio_paths: list[str], tmp_path: Path) -> Path` in
  `rendering/adapters/ffmpeg_video_renderer.py`. Task 7 imports this directly to build the full
  narration audio track the avatar lip-syncs to.

This is a pure refactor — no behavior change. `compose_multi_scene`'s own output must be byte-for-
byte equivalent before and after.

- [ ] **Step 1: Confirm the current call site and method**

Read `rendering/adapters/ffmpeg_video_renderer.py` around line 230
(`audio_concat_path = self._concat_audio(audio_paths, tmp_path)`) and lines 332-372 (the
`_concat_audio` staticmethod body) to confirm line numbers haven't shifted from Task 1-3's edits
(Task 1-3 didn't touch this file, so they should be unchanged, but confirm before editing).

- [ ] **Step 2: Move `_concat_audio`'s body to a module-level function**

Delete the `@staticmethod def _concat_audio(audio_paths, tmp_path)` method from inside
`FfmpegVideoRenderer` (currently around lines 332-372) and add this module-level function instead,
placed near the top of the file after `build_multi_scene_captions` and before the
`class FfmpegVideoRenderer` definition:

```python
def concat_audio_files(audio_paths: list[str], tmp_path: Path) -> Path:
    """Sequential audio concat — no crossfade, no gaps. Total duration
    is exactly the sum of the input files' own durations.

    Re-encodes through a filter graph rather than the concat demuxer's
    `-c copy` stream-copy path — found by direct reproduction that
    stream-copying inputs with DIFFERENT sample rates/channel counts
    (e.g. SarvamTTSProvider's 24kHz mono output mixed with its own
    edge-tts fallback's 44.1kHz stereo output) silently produces a
    corrupted, wrong-duration result with no error. The
    `aresample`+`concat` filter graph normalizes every input to a common
    format before concatenating, so this is correct regardless of which
    TTS vendor served which scene.

    Module-level (not a FfmpegVideoRenderer method) so callers besides
    compose_multi_scene can reuse it — e.g. rendering/multilingual_video.py
    builds the same full-narration audio track this way to feed the
    avatar lip-sync provider."""
    out_path = tmp_path / "audio_concat.wav"
    n = len(audio_paths)

    inputs: list[str] = []
    for p in audio_paths:
        inputs += ["-i", p]

    per_input_filters = "".join(
        f"[{i}:a]aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo[a{i}];"
        for i in range(n)
    )
    concat_inputs = "".join(f"[a{i}]" for i in range(n))
    filter_complex = f"{per_input_filters}{concat_inputs}concat=n={n}:v=0:a=1[out]"

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"audio concat failed: {result.stderr}")
    return out_path
```

- [ ] **Step 3: Update the call site inside `compose_multi_scene`**

Change:
```python
            audio_concat_path = self._concat_audio(audio_paths, tmp_path)
```
to:
```python
            audio_concat_path = concat_audio_files(audio_paths, tmp_path)
```

- [ ] **Step 4: Run the existing regression tests**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_ffmpeg_audio_concat_mismatched_formats.py tests/test_step_e_multi_scene_composition.py -v`
Expected: all PASS, unchanged from before this refactor (confirms `compose_multi_scene`'s behavior
is identical).

- [ ] **Step 5: Add a direct unit test for the extracted function**

Add to `tests/test_ffmpeg_audio_concat_mismatched_formats.py` (append):

```python
def test_concat_audio_files_is_importable_at_module_level(tmp_path):
    """Regression guard for the Task 4 extraction: concat_audio_files
    must be usable without a FfmpegVideoRenderer instance, since
    rendering/multilingual_video.py calls it directly to build the full
    narration audio track for avatar lip-sync."""
    from rendering.adapters.ffmpeg_video_renderer import concat_audio_files
    import subprocess

    # two short real WAV files via ffmpeg's own sine generator - avoids a binary fixture file
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(a)], check=True, timeout=15)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=1.5", str(b)], check=True, timeout=15)

    out_path = concat_audio_files([str(a), str(b)], tmp_path)
    assert out_path.exists()

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out_path)],
        capture_output=True, text=True, timeout=15,
    )
    assert float(probe.stdout.strip()) == pytest.approx(2.5, abs=0.05)
```

(Check the file's existing imports already include `pytest`, `Path`/`tmp_path` fixture usage — add
`import pytest` at the top if it's not already there.)

- [ ] **Step 6: Run it**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_ffmpeg_audio_concat_mismatched_formats.py -v`
Expected: all PASS, including the new test.

- [ ] **Step 7: Commit**

```bash
cd vaanireach
git add rendering/adapters/ffmpeg_video_renderer.py tests/test_ffmpeg_audio_concat_mismatched_formats.py
git commit -m "Extract concat_audio_files() as a reusable module function

Pure refactor - compose_multi_scene's behavior is unchanged (regression
tests pass identically). Needed so rendering/multilingual_video.py can
build the same full-narration audio track the avatar lip-sync provider
needs, without duplicating the aresample/concat filter-graph logic.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `FfmpegVideoRenderer.compose_pip_and_captions()`

**Files:**
- Modify: `rendering/adapters/ffmpeg_video_renderer.py` (add a new method to `FfmpegVideoRenderer`)
- Test: `tests/test_compose_pip_and_captions.py`

**Interfaces:**
- Consumes: `caption_burner.CAPTION_BAR_HEIGHT` (Task 3), `VideoAsset`/`GenerationStatus` (already
  imported in this file), `LanguageCode`.
- Produces: `FfmpegVideoRenderer.compose_pip_and_captions(*, broll_video_path: str, avatar_clip_path: str, caption_track_path: str, duration_seconds: float, project_id: str, storyboard_id: str, language: LanguageCode) -> VideoAsset`.
  Task 7 calls this directly.

Verified during planning: the exact 3-input ffmpeg command below (base video + looped avatar +
caption track, two chained `overlay` filters) was run standalone against dummy inputs and produced a
correct 720x1280 h264/aac MP4 at the exact target duration, correctly truncating a looped
shorter-than-target avatar clip.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compose_pip_and_captions.py`:

```python
"""compose_pip_and_captions: composites a looping avatar PiP box +
burned-in caption track onto an existing B-roll video, in one ffmpeg
pass. Mirrors tests/test_phase4_renderer_smoke.py's dummy-input pattern."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core.models.enums import GenerationStatus, LanguageCode  # noqa: E402
from providers.video.avatar_provider import ensure_fallback_asset  # noqa: E402
from rendering.adapters.ffmpeg_video_renderer import FfmpegVideoRenderer  # noqa: E402

PROJECT_ID = "proj-pip-captions-test"
STORYBOARD_ID = "sb-pip-captions-test"


def _make_dummy_broll(tmp_path: Path, *, duration: float = 5.0) -> str:
    out = tmp_path / "dummy_broll.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"color=green:s=720x1280:d={duration}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
        check=True, timeout=30,
    )
    return str(out)


def _make_dummy_caption_track(tmp_path: Path, *, duration: float = 5.0) -> str:
    from PIL import Image
    frame = Image.new("RGBA", (720, 1280), (0, 0, 0, 0))
    frame_path = tmp_path / "dummy_caption_frame.png"
    frame.save(frame_path)
    out = tmp_path / "dummy_caption_track.mov"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(frame_path),
         "-t", f"{duration}", "-pix_fmt", "yuva420p", "-c:v", "qtrle", str(out)],
        check=True, timeout=30,
    )
    return str(out)


@pytest.fixture(scope="module")
def composed(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("pip_captions")
    broll_path = _make_dummy_broll(tmp_path)
    # deliberately SHORTER than the broll (2s < 5s) - exercises the -stream_loop path
    avatar_path = ensure_fallback_asset(tmp_path / "dummy_avatar.mp4", duration_seconds=2.0)
    caption_path = _make_dummy_caption_track(tmp_path)

    renderer = FfmpegVideoRenderer(output_dir=tmp_path / "out")
    video_asset = renderer.compose_pip_and_captions(
        broll_video_path=broll_path, avatar_clip_path=avatar_path, caption_track_path=caption_path,
        duration_seconds=5.0, project_id=PROJECT_ID, storyboard_id=STORYBOARD_ID, language=LanguageCode.EN,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", video_asset.storage_path_mp4],
        capture_output=True, text=True, timeout=30,
    )
    assert probe.returncode == 0, probe.stderr
    return {"video_asset": video_asset, "probe": json.loads(probe.stdout)}


def test_video_asset_marked_complete(composed):
    assert composed["video_asset"].generation_status == GenerationStatus.COMPLETE
    assert composed["video_asset"].storage_path_mp4 is not None


def test_output_has_expected_dimensions_and_codecs(composed):
    streams = composed["probe"]["streams"]
    video_streams = [s for s in streams if s["codec_type"] == "video"]
    audio_streams = [s for s in streams if s["codec_type"] == "audio"]
    assert len(video_streams) == 1
    assert len(audio_streams) == 1
    assert video_streams[0]["width"] == 720
    assert video_streams[0]["height"] == 1280
    assert video_streams[0]["codec_name"] == "h264"
    assert audio_streams[0]["codec_name"] == "aac"


def test_output_duration_matches_target_despite_shorter_looped_avatar(composed):
    """The avatar clip is 2s, the target is 5s - -stream_loop must fill
    the gap rather than leaving the last 3s without a PiP box, and the
    final output must be capped at exactly the target, not the looped
    avatar's now-longer duration."""
    actual = float(composed["probe"]["format"]["duration"])
    assert actual == pytest.approx(5.0, abs=0.1)


def test_output_decodes_end_to_end_without_errors(composed):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", composed["video_asset"].storage_path_mp4, "-f", "null", "-"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_compose_pip_and_captions.py -v`
Expected: FAIL — `AttributeError: 'FfmpegVideoRenderer' object has no attribute 'compose_pip_and_captions'`.

- [ ] **Step 3: Add `compose_pip_and_captions()` to `FfmpegVideoRenderer`**

Add this import near the top of `rendering/adapters/ffmpeg_video_renderer.py` (alongside the
existing `from core.models.enums import ...` line):

```python
from rendering.adapters.caption_burner import CAPTION_BAR_HEIGHT
```

Add these constants near `FRAME_RATE` at the top of the file:

```python
PIP_WIDTH = 200
PIP_MARGIN = 16
```

Add this method to `FfmpegVideoRenderer`, after `compose_multi_scene` and its helpers (after
`_concat_audio` was removed in Task 4, this goes after `_xfade_chain`/`_probe_duration`, before the
`# ---- VideoRenderer ABC` section):

```python
    # ---------------------------------------------------------------- avatar PiP + caption burn-in

    def compose_pip_and_captions(
        self,
        *,
        broll_video_path: str,
        avatar_clip_path: str,
        caption_track_path: str,
        duration_seconds: float,
        project_id: str,
        storyboard_id: str,
        language: LanguageCode,
    ) -> VideoAsset:
        """Composites an existing B-roll+audio video (compose_multi_scene's
        own output) with a looping avatar PiP box (bottom-left, above the
        caption bar) and a burned-in caption track (rendering/adapters/
        caption_burner.py), in one ffmpeg pass. `-stream_loop -1` on the
        avatar input means a clip shorter than `duration_seconds` (e.g.
        the Tier-3 static fallback) loops seamlessly to cover the whole
        video rather than leaving a blank corner; a real Hedra/D-ID clip
        (audio-driven, already matching `duration_seconds`) effectively
        never repeats. `-t duration_seconds` caps the final output so
        neither a looped avatar nor a slightly-longer (frame-quantized)
        caption track can extend it."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        video_asset = VideoAsset(
            project_id=project_id, storyboard_id=storyboard_id, language=language,
            generation_status=GenerationStatus.IN_PROGRESS,
        )
        out_path = self.output_dir / f"{video_asset.id}.mp4"

        filter_complex = (
            f"[1:v]scale={PIP_WIDTH}:-2[avt];"
            f"[0:v][avt]overlay=x={PIP_MARGIN}:y=H-{CAPTION_BAR_HEIGHT}-h-{PIP_MARGIN}:shortest=0[v1];"
            f"[v1][2:v]overlay=x=0:y=0:shortest=0[vout]"
        )
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", broll_video_path,
            "-stream_loop", "-1", "-i", avatar_clip_path,
            "-i", caption_track_path,
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "0:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration_seconds:.3f}",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            self._job_status[video_asset.id] = GenerationStatus.FAILED
            raise RuntimeError(f"compose_pip_and_captions: ffmpeg composite failed (exit {result.returncode}): {result.stderr}")

        video_asset.storage_path_mp4 = str(out_path)
        video_asset.renderer_name = "ffmpeg-subprocess-pip-captions"
        video_asset.duration_seconds = duration_seconds
        video_asset.generation_status = GenerationStatus.COMPLETE
        self._job_status[video_asset.id] = GenerationStatus.COMPLETE
        return video_asset
```

Confirm `LanguageCode` is already imported in this file (it's used by `_infer_language_or_default`)
— it is, via `from core.models.enums import GenerationStatus, TransitionType` — add `LanguageCode`
to that import line since it's currently only imported inside the local
`_infer_language_or_default` function body, not at module level.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_compose_pip_and_captions.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Run the full renderer regression suite**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_step_e_multi_scene_composition.py tests/test_ffmpeg_audio_concat_mismatched_formats.py tests/test_step_d_single_scene_composition.py -v`
Expected: all PASS unchanged (confirms the new method and import didn't disturb existing behavior).

- [ ] **Step 6: Commit**

```bash
cd vaanireach
git add rendering/adapters/ffmpeg_video_renderer.py tests/test_compose_pip_and_captions.py
git commit -m "Add FfmpegVideoRenderer.compose_pip_and_captions()

One ffmpeg pass: looping avatar PiP box (bottom-left, above the caption
bar, -stream_loop so a short/fallback clip fills the whole duration) +
burned-in caption track, composited onto compose_multi_scene's existing
B-roll+audio output. Verified against dummy inputs including the
shorter-than-target avatar case.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Shared avatar portrait helper

**Files:**
- Create: `providers/video/avatar_portrait.py`
- Test: `tests/test_avatar_portrait.py`

**Interfaces:**
- Consumes: `CloudflareVisualProvider.generate_image(prompt: str, scene: Scene, *, project_id: str) -> MediaAsset` (existing, `providers/visual/cloudflare_provider.py`).
- Produces: `get_avatar_source_image(visual_provider: CloudflareVisualProvider) -> str` (a local file
  path). Task 7 calls this to get the image `AvatarFailoverProvider.generate_avatar_hook()` animates.

- [ ] **Step 1: Write the failing test**

Create `tests/test_avatar_portrait.py`:

```python
"""avatar_portrait: the fixed, shared presenter portrait the template
pipeline's avatar PiP overlay animates - generated once, served from
CloudflareVisualProvider's own LocalCache on every later call."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models.enums import GenerationStatus, MediaAssetType  # noqa: E402
from core.models.media import MediaAsset  # noqa: E402
from providers.video.avatar_portrait import AVATAR_IMAGE_PROMPT, SHARED_ASSET_PROJECT_ID, get_avatar_source_image  # noqa: E402


class _FakeVisualProvider:
    """Records every generate_image call instead of hitting the network -
    fast, deterministic test of avatar_portrait's own logic (prompt/
    project_id/scene shape passed through), not CloudflareVisualProvider's."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []  # (prompt, project_id)

    def generate_image(self, prompt, scene, *, project_id):
        self.calls.append((prompt, project_id))
        return MediaAsset(
            project_id=project_id, scene_id=scene.id, asset_type=MediaAssetType.IMAGE,
            storage_path="/tmp/fake-avatar-portrait.jpg", provider_name="fake",
            generation_status=GenerationStatus.COMPLETE,
        )


def test_get_avatar_source_image_uses_the_fixed_prompt_and_shared_project_id():
    fake = _FakeVisualProvider()
    path = get_avatar_source_image(fake)
    assert path == "/tmp/fake-avatar-portrait.jpg"
    assert len(fake.calls) == 1
    prompt, project_id = fake.calls[0]
    assert prompt == AVATAR_IMAGE_PROMPT
    assert project_id == SHARED_ASSET_PROJECT_ID


def test_calling_twice_still_passes_the_same_prompt_both_times():
    """Regression guard: the prompt must be byte-identical across calls,
    since CloudflareVisualProvider's LocalCache is keyed on the exact
    prompt string - a prompt that drifts (e.g. an interpolated field)
    would silently defeat the cache and hit the network every time."""
    fake = _FakeVisualProvider()
    get_avatar_source_image(fake)
    get_avatar_source_image(fake)
    assert fake.calls[0][0] == fake.calls[1][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_avatar_portrait.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'providers.video.avatar_portrait'`.

- [ ] **Step 3: Write `providers/video/avatar_portrait.py`**

```python
"""avatar_portrait — the fixed, shared presenter portrait the template
pipeline's avatar PiP overlay (rendering/multilingual_video.py) animates
via AvatarFailoverProvider.generate_avatar_hook(). Generated once (keyed
by this exact prompt string in CloudflareVisualProvider's own
LocalCache) and reused across every project/video — "one consistent
on-screen presenter", not scheme-specific content.

Parallel to dashboard/app.py's get_avatar_source_image/AVATAR_IMAGE_PROMPT,
but targeting CloudflareVisualProvider instead of HuggingFaceVisualProvider
— the dashboard pipeline and the template pipeline (this module's caller)
use different VisualProvider implementations, so this is a small, deliberate
duplication rather than a shared import across the two pipelines (see
docs/superpowers/specs/2026-08-20-video-captions-avatar-shortening-design.md,
which keeps the two pipelines independent by design).
"""
from __future__ import annotations

from core.models.enums import NarrativeRole, SceneType
from core.models.storyboard import Scene
from providers.visual.cloudflare_provider import CloudflareVisualProvider

SHARED_ASSET_PROJECT_ID = "vaanireach-shared-assets"
AVATAR_IMAGE_PROMPT = (
    "A friendly, professional Indian government outreach spokesperson: warm, approachable "
    "expression, looking directly at the camera, plain neutral studio background, upper-body "
    "portrait, soft even lighting, photorealistic, no text or logos in frame"
)


def get_avatar_source_image(visual_provider: CloudflareVisualProvider) -> str:
    """Returns a local file path to the presenter portrait — served from
    LocalCache on every call after the first (the cache is keyed on the
    exact AVATAR_IMAGE_PROMPT string), so this only ever hits the
    network once across the process's lifetime."""
    placeholder_scene = Scene(
        storyboard_id="shared-avatar-source", order_index=0, scene_type=SceneType.IMAGE_MOTION,
        narrative_role=NarrativeRole.HOOK,
        narration_segment_text="avatar source portrait", duration_seconds=1.0,
    )
    asset = visual_provider.generate_image(AVATAR_IMAGE_PROMPT, placeholder_scene, project_id=SHARED_ASSET_PROJECT_ID)
    assert asset.storage_path is not None  # generate_image always sets this on success, including its own placeholder-card fallback
    return asset.storage_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_avatar_portrait.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
cd vaanireach
git add providers/video/avatar_portrait.py tests/test_avatar_portrait.py
git commit -m "Add shared avatar portrait helper for the template pipeline

Parallel to dashboard/app.py's get_avatar_source_image, targeting
CloudflareVisualProvider (the template pipeline's visual provider)
instead of HuggingFaceVisualProvider. Same prompt every call, so
CloudflareVisualProvider's LocalCache serves it from disk after the
first generation.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Wire avatar + PiP + captions into `generate_language_video()`

**Files:**
- Modify: `rendering/multilingual_video.py`
- Test: `tests/test_multilingual_video.py` (existing — extend it)

**Interfaces:**
- Consumes: `concat_audio_files()` (Task 4), `compose_pip_and_captions()` (Task 5),
  `build_caption_track()` (Task 3), `get_avatar_source_image()` (Task 6),
  `AvatarFailoverProvider.generate_avatar_hook(image_path, audio_path, *, project_id, scene_id=None, text_prompt="") -> MediaAsset`
  (existing, `providers/video/avatar_provider.py`),
  `CloudflareVisualProvider` (existing, `providers/visual/cloudflare_provider.py`).
- Produces: `LanguageVideoResult` gains one new field, `avatar_composited: bool`. `generate_language_video()`
  gains two new optional keyword parameters: `avatar_provider: AvatarFailoverProvider | None = None`,
  `visual_provider: CloudflareVisualProvider | None = None` (same injection pattern as the existing
  `tts_provider` parameter — real instances by default, injectable fakes in tests).

Per the Global Constraints: this step is **always attempted**, never conditionally skipped. The
`try/except` exists solely to degrade to the plain captioned-B-roll video on a genuine runtime
failure — it logs at ERROR level (loud, not silent) when that happens.

- [ ] **Step 1: Confirm the existing test file's real conventions**

`tests/test_multilingual_video.py` (read in full during planning) has **no existing fake
translator/TTS provider** — its two real tests call `generate_language_video` with a real
`GroqTranslationProvider()` and let `tts_provider` default to a real `SarvamTTSProvider()`, gated
behind `@pytest.mark.skipif(not _HAS_KEYS, ...)` where
`_HAS_KEYS = bool(os.environ.get("GROQ_API_KEY")) and bool(os.environ.get("SARVAM_API_KEYS"))`.
Images come from `PilSceneRenderer` (fast, local, no network). The new tests below follow this exact
same pattern — real translator/TTS, only `avatar_provider`/`visual_provider` faked (those are the
two new parameters this task adds) — rather than inventing parallel fake-everything fixtures.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_multilingual_video.py`:

```python
class _FakeAvatarProvider:
    """generate_avatar_hook without ever calling Hedra/D-ID - returns a
    real short local MP4 (via the same Tier-3 placeholder generator
    AvatarFailoverProvider itself uses) so downstream ffmpeg compositing
    gets a genuinely playable file."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[tuple[str, str]] = []  # (image_path, audio_path)

    def generate_avatar_hook(self, image_path, audio_path, *, project_id, scene_id=None, text_prompt=""):
        from providers.video.avatar_provider import ensure_fallback_asset
        from core.models.enums import GenerationStatus, MediaAssetType
        from core.models.media import MediaAsset

        self.calls.append((image_path, audio_path))
        if self.should_fail:
            raise RuntimeError("simulated avatar provider failure")
        path = ensure_fallback_asset()
        return MediaAsset(
            project_id=project_id, asset_type=MediaAssetType.VIDEO_CLIP, storage_path=path,
            provider_name="fake-avatar", generation_status=GenerationStatus.COMPLETE,
        )


class _FakeVisualProvider:
    def generate_image(self, prompt, scene, *, project_id):
        from core.models.enums import GenerationStatus, MediaAssetType
        from core.models.media import MediaAsset
        return MediaAsset(
            project_id=project_id, scene_id=scene.id, asset_type=MediaAssetType.IMAGE,
            storage_path=_dummy_portrait_path(), provider_name="fake-visual",
            generation_status=GenerationStatus.COMPLETE,
        )


def _dummy_portrait_path() -> str:
    """A real, tiny JPEG - Hedra/D-ID would need a real image, but the
    fake avatar provider above never actually reads this file, so any
    valid image on disk is fine for these tests."""
    import tempfile
    from PIL import Image
    path = Path(tempfile.gettempdir()) / "test_avatar_portrait.jpg"
    if not path.exists():
        Image.new("RGB", (200, 280), (128, 128, 128)).save(path, format="JPEG")
    return str(path)


@pytest.mark.skipif(not _HAS_KEYS, reason="GROQ_API_KEY/SARVAM_API_KEYS not set")
def test_generate_language_video_composites_avatar_and_captions_by_default():
    """Global constraint: avatar+PiP+caption compositing is REQUIRED, not
    optional - a normal successful run must produce avatar_composited=True."""
    facts = sample_notice_facts()
    director = TemplateStoryDirector()
    _, scenes = director.plan_narrative_arc(facts)
    renderer = PilSceneRenderer()
    image_paths = [renderer.render_scene(s).storage_path for s in scenes]

    fake_avatar = _FakeAvatarProvider()
    fake_visual = _FakeVisualProvider()

    result = generate_language_video(
        facts, image_paths,
        story_director=director, translator=GroqTranslationProvider(),
        target_language=LanguageCode.EN, project_id="test-avatar-wiring",
        avatar_provider=fake_avatar, visual_provider=fake_visual,
    )
    assert result.avatar_composited is True
    assert len(fake_avatar.calls) == 1
    assert result.video_asset.storage_path_mp4 is not None
    assert Path(result.video_asset.storage_path_mp4).exists()


@pytest.mark.skipif(not _HAS_KEYS, reason="GROQ_API_KEY/SARVAM_API_KEYS not set")
def test_generate_language_video_falls_back_when_compositing_fails():
    """Reliability fallback (not a design option - see Global Constraints):
    when the avatar/compositing step raises, the run must still return a
    valid, playable video (the plain captioned B-roll), not crash."""
    facts = sample_notice_facts()
    director = TemplateStoryDirector()
    _, scenes = director.plan_narrative_arc(facts)
    renderer = PilSceneRenderer()
    image_paths = [renderer.render_scene(s).storage_path for s in scenes]

    fake_avatar = _FakeAvatarProvider(should_fail=True)
    fake_visual = _FakeVisualProvider()

    result = generate_language_video(
        facts, image_paths,
        story_director=director, translator=GroqTranslationProvider(),
        target_language=LanguageCode.EN, project_id="test-avatar-fallback",
        avatar_provider=fake_avatar, visual_provider=fake_visual,
    )
    assert result.avatar_composited is False
    assert result.video_asset.storage_path_mp4 is not None
    assert Path(result.video_asset.storage_path_mp4).exists()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_multilingual_video.py -v -k avatar`
Expected: FAIL — `TypeError: generate_language_video() got an unexpected keyword argument 'avatar_provider'`
(or SKIPPED if `GROQ_API_KEY`/`SARVAM_API_KEYS` aren't set in this environment — in that case Steps 4
onward still apply, just verify via Task 9's end-to-end run instead).

- [ ] **Step 4: Update `rendering/multilingual_video.py`**

Add imports (alongside the existing ones):

```python
import logging
import tempfile
from pathlib import Path

from providers.video.avatar_portrait import get_avatar_source_image
from providers.video.avatar_provider import AvatarFailoverProvider
from providers.visual.cloudflare_provider import CloudflareVisualProvider
from rendering.adapters.caption_burner import build_caption_track
from rendering.adapters.ffmpeg_video_renderer import FfmpegVideoRenderer, build_multi_scene_captions, concat_audio_files

logger = logging.getLogger("vaanireach.rendering.multilingual_video")

BROLL_WIDTH = 720
BROLL_HEIGHT = 1280
```

Add the new field to `LanguageVideoResult`:

```python
@dataclass
class LanguageVideoResult:
    language: LanguageCode
    video_asset: VideoAsset
    srt_text: str
    vtt_text: str
    scenes: list[Scene]
    verified_count: int
    blocking_count: int
    avatar_composited: bool
    """True when the avatar PiP overlay + caption burn-in succeeded for
    this language. False means the run degraded to the plain
    captioned-B-roll video after a compositing failure — see the
    Global Constraints in docs/superpowers/plans/2026-08-20-video-captions-avatar-shortening.md:
    this is a reliability fallback, never an accepted steady state, and
    the caller (tests/demo_multilingual_video.py) reports it loudly."""
```

Replace the function signature and body:

```python
def generate_language_video(
    facts: list[SourceFact],
    image_paths: list[str],
    *,
    story_director: StoryDirector,
    translator: TranslationProvider,
    target_language: LanguageCode,
    project_id: str,
    tts_provider: SarvamTTSProvider | None = None,
    avatar_provider: AvatarFailoverProvider | None = None,
    visual_provider: CloudflareVisualProvider | None = None,
) -> LanguageVideoResult:
    """image_paths must already be rendered (same order as
    story_director.plan_narrative_arc(facts)'s scenes) — this function
    doesn't regenerate images, it only varies narration/audio/timing per
    language, per the module docstring.

    Avatar PiP overlay + caption burn-in are ALWAYS attempted (required
    MVP scope — see the Global Constraints doc referenced above), never
    conditionally skipped. Only a genuine runtime failure in that step
    falls back to the plain captioned-B-roll video; that fallback is a
    reliability net, not a design option, and it logs loudly."""
    _, scenes = story_director.plan_narrative_arc(facts)
    if len(scenes) != len(image_paths):
        raise ValueError(
            f"image_paths has {len(image_paths)} entries but plan_narrative_arc produced {len(scenes)} scenes — "
            "image_paths must be pre-rendered for these exact scenes, in order"
        )

    if target_language != LanguageCode.EN:
        scenes = translate_scenes(scenes, translator, target_language=target_language)

    tts = tts_provider or SarvamTTSProvider()
    audio_paths: list[str] = []
    for scene in scenes:
        audio_asset = tts.synthesize(scene.narration_segment_text, target_language, project_id=project_id)
        scene.duration_seconds = audio_asset.duration_seconds  # real per-language duration is authoritative
        audio_paths.append(audio_asset.storage_path)

    claims = claims_from_scenes(scenes, project_id=project_id, language=target_language)
    results = DeterministicFactVerifier().verify_batch(claims, facts)
    verified_count = sum(1 for r in results if r.status == VerificationStatus.VERIFIED)
    blocking_count = sum(1 for r in results if r.is_blocking)

    renderer = FfmpegVideoRenderer()
    broll_video_asset = renderer.compose_multi_scene(
        scenes=scenes, image_paths=image_paths, audio_paths=audio_paths, project_id=project_id,
    )
    srt_text, vtt_text = build_multi_scene_captions(scenes)

    video_asset = broll_video_asset
    avatar_composited = False
    avatar = avatar_provider or AvatarFailoverProvider()
    visual = visual_provider or CloudflareVisualProvider()
    try:
        with tempfile.TemporaryDirectory(prefix="avatar_pip_") as tmp:
            tmp_path = Path(tmp)
            full_audio_path = concat_audio_files(audio_paths, tmp_path)
            avatar_portrait_path = get_avatar_source_image(visual)
            full_narration_text = " ".join(s.narration_segment_text for s in scenes)[:300]
            avatar_asset = avatar.generate_avatar_hook(
                avatar_portrait_path, str(full_audio_path), project_id=project_id,
                text_prompt=full_narration_text,
            )
            caption_track_path = build_caption_track(
                scenes, language=target_language, width=BROLL_WIDTH, height=BROLL_HEIGHT, tmp_dir=tmp_path,
            )
            video_asset = renderer.compose_pip_and_captions(
                broll_video_path=broll_video_asset.storage_path_mp4,
                avatar_clip_path=avatar_asset.storage_path,
                caption_track_path=str(caption_track_path),
                duration_seconds=broll_video_asset.duration_seconds,
                project_id=project_id, storyboard_id=broll_video_asset.storyboard_id,
                language=target_language,
            )
            avatar_composited = True
    except Exception as exc:  # noqa: BLE001 - a compositing failure must degrade, never crash the run
        logger.error(
            "generate_language_video: avatar+PiP+caption compositing failed for language=%s (%s) — "
            "falling back to the plain captioned-sidecar B-roll video. This is a reliability fallback, "
            "not an accepted steady state — investigate if this triggers on a real run.",
            target_language.value, exc,
        )

    return LanguageVideoResult(
        language=target_language, video_asset=video_asset, srt_text=srt_text, vtt_text=vtt_text,
        scenes=scenes, verified_count=verified_count, blocking_count=blocking_count,
        avatar_composited=avatar_composited,
    )
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_multilingual_video.py -v -k avatar`
Expected: both new tests PASS.

- [ ] **Step 6: Run the full existing test file for regressions**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m pytest tests/test_multilingual_video.py -v`
Expected: all tests PASS, including every pre-existing test (the new `avatar_provider`/
`visual_provider` parameters default to real instances, so any existing test that doesn't pass them
now exercises the full real avatar path unless it already injects fakes for everything else — if
any pre-existing test fails because it now reaches real `AvatarFailoverProvider()`/
`CloudflareVisualProvider()` construction, that test needs the same `_FakeAvatarProvider`/
`_FakeVisualProvider` fakes added to its own call, matching Step 2's pattern).

- [ ] **Step 7: Commit**

```bash
cd vaanireach
git add rendering/multilingual_video.py tests/test_multilingual_video.py
git commit -m "Wire avatar PiP overlay + caption burn-in into generate_language_video

Always attempted (required MVP scope, never conditionally skipped) -
full narration audio -> avatar lip-sync -> caption track -> PiP+caption
compositing, wrapped in a try/except that degrades to the plain
captioned-B-roll video only on a genuine runtime failure, logged loudly.
LanguageVideoResult gains avatar_composited: bool so callers can tell
which path a given run took.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Report compositing status in the demo script

**Files:**
- Modify: `tests/demo_multilingual_video.py`

**Interfaces:**
- Consumes: `LanguageVideoResult.avatar_composited` (Task 7).
- Produces: nothing new consumed by other tasks — this is the final human-facing reporting layer.

- [ ] **Step 1: Add avatar-compositing status to the per-language output**

In the `for lang in languages:` loop, after the existing `print(f"  verification: ...")` line and
before the blank `print()`, add:

```python
        status_marker = "✅" if result.avatar_composited else "⚠️  DEGRADED (no avatar/captions)"
        print(f"  avatar+captions: {status_marker}")
```

- [ ] **Step 2: Add it to the SUMMARY section too**

Change:
```python
    print("=== SUMMARY ===")
    for r in results:
        print(f"  {r.language.value}: {r.video_asset.storage_path_mp4}  ({r.verified_count}/{len(r.scenes)} verified)")
```
to:
```python
    print("=== SUMMARY ===")
    for r in results:
        status = "avatar+captions OK" if r.avatar_composited else "DEGRADED - plain B-roll only, no avatar/captions"
        print(f"  {r.language.value}: {r.video_asset.storage_path_mp4}  ({r.verified_count}/{len(r.scenes)} verified)  [{status}]")

    degraded = [r.language.value for r in results if not r.avatar_composited]
    if degraded:
        print(f"\n⚠️  {len(degraded)} language(s) fell back to plain B-roll (no avatar/captions): {degraded}")
        print("    This is a reliability fallback triggering, not expected steady-state — investigate.")
```

- [ ] **Step 3: Verify the script still runs (manual — no API keys required for this check alone)**

Run: `cd vaanireach && PYTHONPATH=. .venv/bin/python3 -m py_compile tests/demo_multilingual_video.py`
Expected: no output, exit code 0 (confirms no syntax errors — the real run with API keys happens in
Task 9).

- [ ] **Step 4: Commit**

```bash
cd vaanireach
git add tests/demo_multilingual_video.py
git commit -m "Report avatar/caption compositing status in the multilingual demo

A degraded (no-avatar) run is now loudly visible in both per-language
and SUMMARY output, not silently indistinguishable from success - per
the Global Constraints, that fallback triggering is a signal to
investigate, not an accepted result.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: End-to-end verification run

**Files:** none (verification only — no code changes).

**Interfaces:** none produced; this task confirms Tasks 1-8 work together against the real pipeline
with real API keys, per the spec's build-priority item 6 ("Final verification").

- [ ] **Step 1: Confirm required API keys are configured**

```bash
cd vaanireach
grep -q "GROQ_API_KEY" .env && grep -q "SARVAM_API_KEYS" .env && echo "translation/TTS keys present"
grep -qE "HEDRA_API_KEYS|DID_API_KEYS" .env && echo "at least one avatar vendor key present" || echo "NO avatar vendor keys - run will exercise the Tier-3 static-fallback path, which is fine but won't demonstrate real lip-sync"
```

- [ ] **Step 2: Run the full three-language demo**

```bash
cd vaanireach
PYTHONPATH=. .venv/bin/python3 tests/demo_multilingual_video.py en hi mr
```

Expected: the script completes without raising, and its `=== SUMMARY ===` section (Task 8) shows
`avatar+captions OK` for all three languages, with no `⚠️  N language(s) fell back` warning. Note
each printed `MP4:` path.

- [ ] **Step 3: Verify each output video's shape**

For each of the three printed MP4 paths:

```bash
ffprobe -v error -show_entries format=duration:stream=width,height,codec_name -of default=noprint_wrappers=0 <path>
ffmpeg -v error -i <path> -f null - && echo "decodes cleanly"
```

Expected per video: `width=720`, `height=1280`, a video stream `codec_name=h264` and an audio stream
`codec_name=aac`, `duration` in the 20-35s range (best-effort per the Global Constraints — the
sample document's real audio-driven duration will differ slightly from the ~22.6s pre-TTS estimate
per language, since spoken length varies by language), and clean decode with no ffmpeg errors.

- [ ] **Step 4: Visually confirm the avatar box and captions are actually present**

Open at least one of the three output videos (e.g. `open <path>` on macOS) and confirm by eye:
- A small talking-avatar box is visible in the bottom-left corner for the entire video (not just an
  intro segment, not blank after a few seconds).
- A caption bar is visible near the bottom, above the avatar box, showing readable text that changes
  as scenes change (for `hi`/`mr`: readable Devanagari text, not empty boxes/tofu).

This is the step the spec's "Final verification" priority item calls for — an automated ffprobe
check alone can't confirm the avatar/captions are genuinely legible, only that the file is
well-formed.

- [ ] **Step 5: Run the full test suite once more for a clean baseline**

```bash
cd vaanireach
PYTHONPATH=. .venv/bin/python3 -m pytest tests/ -v --ignore=tests/demo_multilingual_video.py --ignore=tests/demo_cloudflare_contextual_video.py --ignore=tests/demo_step_e_full_video.py -x
```

(Demo scripts are excluded — they're manual/expensive, exercised directly in Steps 2-4, not part of
the automated suite.)

Expected: all PASS.

- [ ] **Step 6: Report the result**

No commit for this task (verification only) — report back: which languages showed `avatar+captions
OK`, the measured duration of each, and confirmation (or not) of the visual check in Step 4. If any
language fell back to the degraded path, that's a real bug to fix before considering this plan done
— per the Global Constraints, it is never an acceptable steady state.

---

## Self-Review Notes

**Spec coverage:** every Decision in the spec maps to a task — avatar placement/lip-sync (Tasks 6-7),
avatar art style (Task 6's `AVATAR_IMAGE_PROMPT`), captions bottom-bar + sidecars kept (Tasks 3, 5,
7 all preserve `build_multi_scene_captions`), duration/CTA-CLOSING drop (Task 1). The spec's
"required, not optional" framing and 6-item build-priority list are both captured verbatim in Global
Constraints and this plan's task ordering (1 → 4/5 → 6 → 7 → 9 mirrors base-video →
narration/compositing → avatar → PiP → captions → verification).

**Placeholder scan:** no TBD/TODO; every step has real, complete code or an exact verified shell
command (font URLs, byte sizes, and ffmpeg commands were run for real during planning, not guessed).

**Type/signature consistency:** `compose_pip_and_captions` keyword args match exactly between Task 5
(definition) and Task 7 (call site) — `broll_video_path`, `avatar_clip_path`, `caption_track_path`,
`duration_seconds`, `project_id`, `storyboard_id`, `language`. `build_caption_track`'s signature
matches between Task 3 (definition) and Task 7 (call site) — `scenes`, `language`, `width`, `height`,
`tmp_dir`. `get_avatar_source_image(visual_provider)` matches between Task 6 and Task 7.
`concat_audio_files(audio_paths, tmp_path) -> Path` matches between Task 4 (definition/call-site
update) and Task 7 (new call site). `CAPTION_BAR_HEIGHT` is defined once (Task 3) and only ever
referenced (Task 5), never redefined.

**Deviation from spec, documented:** the spec's Components section named ffmpeg's `subtitles` filter
for caption burn-in; Task 3's docstring and this plan's Global Constraints record why that changed to
a Pillow-rendered alpha video track (this ffmpeg build has no libass/libfreetype — verified, not
assumed). The user-facing decisions the spec locked in (bottom-bar placement, sidecars kept, no
speech bubble) are all still exactly honored; only the ffmpeg-level mechanism changed.
