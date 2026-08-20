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

from dataclasses import dataclass

from core.interfaces.story_director import StoryDirector
from core.interfaces.translation_provider import TranslationProvider
from core.models.enums import LanguageCode, VerificationStatus
from core.models.fact import SourceFact
from core.models.media import VideoAsset
from core.models.storyboard import Scene
from providers.tts.sarvam_tts_provider import SarvamTTSProvider
from providers.translation.groq_translation_provider import translate_scenes
from providers.verification.deterministic_fact_verifier import DeterministicFactVerifier, claims_from_scenes
from rendering.adapters.ffmpeg_video_renderer import FfmpegVideoRenderer, build_multi_scene_captions


@dataclass
class LanguageVideoResult:
    language: LanguageCode
    video_asset: VideoAsset
    srt_text: str
    vtt_text: str
    scenes: list[Scene]
    verified_count: int
    blocking_count: int


def generate_language_video(
    facts: list[SourceFact],
    image_paths: list[str],
    *,
    story_director: StoryDirector,
    translator: TranslationProvider,
    target_language: LanguageCode,
    project_id: str,
    tts_provider: SarvamTTSProvider | None = None,
) -> LanguageVideoResult:
    """image_paths must already be rendered (same order as
    story_director.plan_narrative_arc(facts)'s scenes) — this function
    doesn't regenerate images, it only varies narration/audio/timing per
    language, per the module docstring."""
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

    video_asset = FfmpegVideoRenderer().compose_multi_scene(
        scenes=scenes, image_paths=image_paths, audio_paths=audio_paths, project_id=project_id,
    )
    srt_text, vtt_text = build_multi_scene_captions(scenes)

    return LanguageVideoResult(
        language=target_language, video_asset=video_asset, srt_text=srt_text, vtt_text=vtt_text,
        scenes=scenes, verified_count=verified_count, blocking_count=blocking_count,
    )
