"""generate_language_video — the "select a language" entry point.

Builds the NarrativeArc and contextual images ONCE (language-independent
— per docs/superpowers/specs/2026-08-20-narrative-story-director-design.md's
own language-independence principle, images are visual/contextual, not
tied to narration language). For each requested language, this function
only varies the narration: translated (via GroqTranslationProvider,
skipped for English), synthesized in that language (SarvamTTSProvider,
which covers hi/mr/ta/bn/te/kn/ml/gu directly), and re-verified against
the same English-language Source Fact Ledger.

"The video is the same, just different audio" in practice: identical
images, identical story structure/transitions, but the video is
RECOMPOSED per language rather than one file with a swapped audio track
— different languages take different real time to speak the same
content, so each language's own TTS-measured durations must drive its
own Ken-Burns/transition timing to stay in sync. Visually identical,
audio genuinely correct.
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.interfaces.story_director import StoryDirector
from core.interfaces.translation_provider import TranslationProvider
from core.models.enums import LanguageCode, VerificationStatus
from core.models.fact import SourceFact
from core.models.media import VideoAsset
from core.models.storyboard import Scene
from providers.tts.sarvam_tts_provider import SarvamTTSProvider
from providers.translation.groq_translation_provider import translate_scenes
from providers.verification.deterministic_fact_verifier import DeterministicFactVerifier, claims_from_scenes
from providers.video.avatar_portrait import get_avatar_source_image
from providers.video.avatar_provider import AvatarFailoverProvider
from providers.visual.cloudflare_provider import CloudflareVisualProvider
from rendering.adapters.caption_burner import build_caption_track
from rendering.adapters.ffmpeg_video_renderer import FfmpegVideoRenderer, build_multi_scene_captions, concat_audio_files

logger = logging.getLogger("vaanireach.rendering.multilingual_video")

BROLL_WIDTH = 720
BROLL_HEIGHT = 1280


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
