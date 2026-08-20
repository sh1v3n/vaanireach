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
import uuid
from dataclasses import dataclass
from pathlib import Path

from core.interfaces.story_director import StoryDirector
from core.interfaces.translation_provider import TranslationProvider
from core.models.document import DocumentPage
from core.models.enums import LanguageCode, VerificationStatus
from core.models.fact import SourceFact
from core.models.media import VideoAsset
from core.models.storyboard import Scene
from providers.llm.groq_provider import GroqLLMProvider
from providers.narrative.dynamic_narration import generate_dynamic_narration
from providers.narrative.template_story_director import TemplateStoryDirector
from providers.tts.sarvam_tts_provider import SarvamTTSProvider
from providers.translation.groq_translation_provider import GroqTranslationProvider, translate_scenes
from providers.verification.deterministic_fact_verifier import DeterministicFactVerifier, claims_from_scenes
from providers.video.avatar_portrait import get_avatar_source_image
from providers.video.avatar_provider import AvatarFailoverProvider
from providers.visual.cloudflare_provider import CloudflareVisualProvider
from rendering.adapters.caption_burner import build_caption_track
from rendering.adapters.cloudflare_scene_renderer import render_scene_images
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
    avatar_tier: int | None = None
    """The `tier` from the generated avatar MediaAsset's metadata
    (1=Hedra, 2=D-ID, 3=Tier-3 static local placeholder — see
    providers/video/avatar_provider.py) when avatar_composited is True.
    None when avatar_composited is False (the whole avatar+compositing
    step failed, so no avatar asset exists). A Tier-3 result means
    compositing itself succeeded but the "avatar" is a static dark
    rectangle with no face/lip-sync — AvatarFailoverProvider never
    raises even when both real vendors are exhausted, so callers must
    check this field (not just avatar_composited) to know whether a run
    produced a real talking avatar."""


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
    precomputed_scenes: list[Scene] | None = None,
) -> LanguageVideoResult:
    """image_paths must already be rendered (same order as
    story_director.plan_narrative_arc(facts)'s scenes, or as
    precomputed_scenes if given) — this function doesn't regenerate
    images, it only varies narration/audio/timing per language, per the
    module docstring.

    precomputed_scenes lets a caller (run_full_pipeline) supply
    already-planned, English-language scenes — e.g. ones that have been
    through generate_dynamic_narration's LLM rewrite + per-scene fact
    verification — instead of this function silently recomputing fresh
    deterministic scenes from story_director.plan_narrative_arc every
    call, which would discard that rewrite. Each call gets its own deep
    copy (model_copy) before any mutation, the same copy-on-write
    discipline translate_scenes already uses, so concurrent per-language
    calls sharing one precomputed_scenes list never step on each other.
    Omitted (the default), this behaves exactly as before.

    Avatar PiP overlay + caption burn-in are ALWAYS attempted (required
    MVP scope — see the Global Constraints doc referenced above), never
    conditionally skipped. Only a genuine runtime failure in that step
    falls back to the plain captioned-B-roll video; that fallback is a
    reliability net, not a design option, and it logs loudly."""
    if precomputed_scenes is not None:
        scenes = [s.model_copy() for s in precomputed_scenes]
    else:
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
        width=BROLL_WIDTH, height=BROLL_HEIGHT,
    )
    srt_text, vtt_text = build_multi_scene_captions(scenes)

    video_asset = broll_video_asset
    avatar_composited = False
    avatar_tier: int | None = None
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
            avatar_tier = avatar_asset.metadata.get("tier")
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
        avatar_tier = None
        logger.error(
            "generate_language_video: avatar+PiP+caption compositing failed for language=%s (%s) — "
            "falling back to the plain captioned-sidecar B-roll video. This is a reliability fallback, "
            "not an accepted steady state — investigate if this triggers on a real run.",
            target_language.value, exc,
        )

    return LanguageVideoResult(
        language=target_language, video_asset=video_asset, srt_text=srt_text, vtt_text=vtt_text,
        scenes=scenes, verified_count=verified_count, blocking_count=blocking_count,
        avatar_composited=avatar_composited, avatar_tier=avatar_tier,
    )


def run_full_pipeline(
    document_text: str,
    *,
    languages: list[LanguageCode],
    project_id: str,
    llm_provider: GroqLLMProvider | None = None,
    story_director: StoryDirector | None = None,
    translator: TranslationProvider | None = None,
    visual_provider: CloudflareVisualProvider | None = None,
    tts_provider: SarvamTTSProvider | None = None,
    avatar_provider: AvatarFailoverProvider | None = None,
) -> list[LanguageVideoResult]:
    """The real "raw document text in, videos out" entry point —
    replaces the hardcoded sample_notice_facts() fixture every test/demo
    has used so far with genuine fact extraction (GroqLLMProvider.extract_facts,
    ported from a teammate's branch — see
    docs/superpowers/specs/2026-08-20-video-captions-avatar-shortening-design.md's
    sibling work for why only extract_facts was taken and not that
    branch's own rendering/avatar code). Everything downstream of
    extraction (narrative planning, B-roll, translation, TTS, avatar,
    captions, composition) is this pipeline's own existing, tested code,
    unchanged.

    Narration is drafted dynamically per scene (providers/narrative/
    dynamic_narration.py's generate_dynamic_narration), grounded ONLY in
    that scene's own cited facts and independently re-verified against
    the real fact ledger before acceptance — replacing
    TemplateStoryDirector's hardcoded per-role sentence templates (e.g.
    the "Farmers of X" bug: a fixed audience assumption with nothing to
    do with a tax/health/education document's real content). Falls back
    to those deterministic templates, per scene, on any LLM failure or
    failed verification — this function never raises for that reason.

    B-roll images are rendered ONCE (shared across every language, per
    generate_language_video's own language-independence principle) using
    fact-aware prompts when possible (rendering/adapters/
    cloudflare_scene_renderer.py's render_scene_images) — so a document
    about a genuinely different topic gets visually appropriate B-roll,
    not the static per-role templates' hardcoded farmer/agriculture
    scenes. Falls back to those static templates automatically if the
    fact-aware LLM call fails; this function never raises for that
    reason, only for zero-facts-extracted (nothing to make a video from).

    Both dynamic passes (narration, then B-roll prompts, in that order —
    B-roll prompts are grounded in the FINAL narration text) run ONCE, in
    English, before the per-language loop; every language then
    translates from this same narration, exactly like the deterministic
    baseline (StoryDirector's language-independence principle)."""
    llm = llm_provider or GroqLLMProvider()
    director = story_director or TemplateStoryDirector()
    trans = translator or GroqTranslationProvider()
    visual = visual_provider or CloudflareVisualProvider()

    document_id = str(uuid.uuid4())
    pages = [DocumentPage(document_id=document_id, page_number=1, raw_text=document_text)]
    facts = llm.extract_facts(document_id, pages, project_id=project_id)
    if not facts:
        raise ValueError(
            "run_full_pipeline: extract_facts returned zero facts — nothing to build a video from. "
            "Check GROQ_API_KEY(S) and that document_text actually contains extractable scheme/policy content."
        )

    _, scenes = director.plan_narrative_arc(facts)
    scenes = generate_dynamic_narration(scenes, facts, project_id=project_id)
    image_paths = render_scene_images(scenes, visual, project_id=project_id)

    return [
        generate_language_video(
            facts, image_paths, story_director=director, translator=trans,
            target_language=lang, project_id=project_id,
            tts_provider=tts_provider, avatar_provider=avatar_provider, visual_provider=visual,
            precomputed_scenes=scenes,
        )
        for lang in languages
    ]
