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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.interfaces.story_director import StoryDirector
from core.interfaces.translation_provider import TranslationProvider
from core.models.document import DocumentPage
from core.models.enums import LanguageCode, VerificationStatus
from core.models.fact import SourceFact
from core.models.media import VideoAsset
from core.models.verification import VerificationResult
from core.models.storyboard import Scene
from providers.llm.groq_provider import GroqLLMProvider
from providers.narrative.dynamic_narration import DEFAULT_PACE_FOR_STYLE, NarrationStyle, generate_dynamic_narration
from providers.narrative.template_story_director import TemplateStoryDirector
from providers.tts.sarvam_tts_provider import SarvamTTSProvider
from providers.tts.sarvam_voices import DEFAULT_SPEAKER as SARVAM_DEFAULT_SPEAKER, VoiceGender, gender_for_voice
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
    facts: list[SourceFact]
    """The full Source Fact Ledger this language's narration was
    verified against — identical across every language in the same
    run_full_pipeline call (facts are extracted once, shared, per the
    language-independence principle). Exists so a caller (e.g. a review
    UI) can show the officer what was actually extracted from the
    source document without re-running extraction."""
    image_paths: list[str]
    """The B-roll image paths this language's video was composed from —
    identical across every language in the same run for the same
    reason as `facts`. Exists so a caller can re-invoke
    generate_language_video later (e.g. after an edited narration line)
    without re-rendering images or re-extracting facts."""
    verified_count: int
    blocking_count: int
    verification_results: list[VerificationResult]
    """Per-claim detail behind verified_count/blocking_count above —
    one VerificationResult per scene, in the same order as `scenes`.
    Exists so a caller can show which specific scene(s) failed
    verification and why, not just the aggregate counts."""
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
    speaker: str = SARVAM_DEFAULT_SPEAKER,
    pace: float = 1.0,
    pitch: float | None = None,
    voice_gender: VoiceGender = "male",
) -> LanguageVideoResult:
    """image_paths must already be rendered (same order as
    story_director.plan_narrative_arc(facts)'s scenes, or as
    precomputed_scenes if given) — this function doesn't regenerate
    images, it only varies narration/audio/timing per language, per the
    module docstring.

    speaker/pace/pitch pass straight through to SarvamTTSProvider
    (pitch is silently ignored by Sarvam unless speaker resolves to a
    bulbul:v2 voice — see providers/tts/sarvam_voices.py). voice_gender
    is NOT derived from speaker here — the caller (run_full_pipeline,
    or the backend) is responsible for that, keeping this module free
    of voice-roster knowledge; it only decides which avatar portrait
    (get_avatar_source_image) to use.

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

    # Captions (sidecar SRT/VTT and burned-in) are ALWAYS English, regardless
    # of target_language — only the spoken AUDIO varies per language. Kept as
    # a separate copy before translation so a Hindi/Marathi/etc. video still
    # reads its captions in English. Durations get synced to the real,
    # TTS-measured timing below so captions stay in sync with whichever
    # language's audio actually plays.
    english_scenes = [s.model_copy() for s in scenes]

    if target_language != LanguageCode.EN:
        scenes = translate_scenes(scenes, translator, target_language=target_language)

    tts = tts_provider or SarvamTTSProvider()
    audio_paths: list[str] = []
    for scene, english_scene in zip(scenes, english_scenes):
        audio_asset = tts.synthesize(
            scene.narration_segment_text, target_language, f"sarvam:{speaker}",
            project_id=project_id, pace=pace, pitch=pitch,
        )
        scene.duration_seconds = audio_asset.duration_seconds  # real per-language duration is authoritative
        english_scene.duration_seconds = audio_asset.duration_seconds  # captions must match the actual audio timing
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
    srt_text, vtt_text = build_multi_scene_captions(english_scenes)

    video_asset = broll_video_asset
    avatar_composited = False
    avatar_tier: int | None = None
    avatar = avatar_provider or AvatarFailoverProvider()
    visual = visual_provider or CloudflareVisualProvider()
    try:
        with tempfile.TemporaryDirectory(prefix="avatar_pip_") as tmp:
            tmp_path = Path(tmp)
            full_audio_path = concat_audio_files(audio_paths, tmp_path)
            avatar_portrait_path = get_avatar_source_image(visual, voice_gender)
            full_narration_text = " ".join(s.narration_segment_text for s in scenes)[:300]
            avatar_asset = avatar.generate_avatar_hook(
                avatar_portrait_path, str(full_audio_path), project_id=project_id,
                text_prompt=full_narration_text,
            )
            avatar_tier = avatar_asset.metadata.get("tier")
            caption_track_path = build_caption_track(
                english_scenes, language=LanguageCode.EN, width=BROLL_WIDTH, height=BROLL_HEIGHT, tmp_dir=tmp_path,
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
        scenes=scenes, facts=facts, image_paths=image_paths,
        verified_count=verified_count, blocking_count=blocking_count, verification_results=results,
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
    on_stage: Callable[[str, dict], None] | None = None,
    speaker: str = SARVAM_DEFAULT_SPEAKER,
    style: NarrationStyle = "news",
    pace: float | None = None,
    pitch: float | None = None,
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
    baseline (StoryDirector's language-independence principle).

    on_stage, if given, is called synchronously at each major step —
    (stage_name, data) — purely for progress observability (e.g. a
    caller updating a job record's status field for a UI to poll). Never
    called concurrently, never affects control flow, defaults to a
    no-op: existing callers that don't pass it see zero behavior
    change. Stage names: "extracting_facts", "facts_extracted" (data
    includes the extracted `facts` list — the earliest point a caller
    can show the user anything, well before narration/images/video are
    ready), "planning_narrative", "drafting_narration",
    "rendering_images", "generating_video" (data includes `language`,
    fired once per requested language, in order).

    speaker selects the Sarvam voice (see providers/tts/sarvam_voices.py
    for the curated, gender-tagged roster) — the avatar's on-screen
    gender automatically follows it (voices_by_gender/gender_for_voice),
    so a female voice always gets a female avatar portrait and vice
    versa. style picks the narration's writing register (news vs
    storytelling — see dynamic_narration.py's NarrationStyle; Sarvam's
    TTS API has no native style parameter, so this is the only place a
    style choice can actually change anything). pace, if omitted,
    defaults to a style-appropriate value (DEFAULT_PACE_FOR_STYLE) —
    pass an explicit value to override. pitch is silently ignored by
    Sarvam unless speaker resolves to a bulbul:v2 voice."""
    def _stage(name: str, **data: object) -> None:
        if on_stage is not None:
            on_stage(name, data)

    llm = llm_provider or GroqLLMProvider()
    director = story_director or TemplateStoryDirector()
    trans = translator or GroqTranslationProvider()
    visual = visual_provider or CloudflareVisualProvider()
    resolved_pace = pace if pace is not None else DEFAULT_PACE_FOR_STYLE[style]
    voice_gender = gender_for_voice(speaker)

    document_id = str(uuid.uuid4())
    pages = [DocumentPage(document_id=document_id, page_number=1, raw_text=document_text)]
    _stage("extracting_facts")
    facts = llm.extract_facts(document_id, pages, project_id=project_id)
    if not facts:
        raise ValueError(
            "run_full_pipeline: extract_facts returned zero facts — nothing to build a video from. "
            "Check GROQ_API_KEY(S) and that document_text actually contains extractable scheme/policy content."
        )
    _stage("facts_extracted", facts=facts)

    _stage("planning_narrative")
    _, scenes = director.plan_narrative_arc(facts)
    _stage("drafting_narration")
    scenes = generate_dynamic_narration(scenes, facts, project_id=project_id, style=style)
    _stage("rendering_images")
    image_paths = render_scene_images(scenes, visual, project_id=project_id)

    results = []
    for lang in languages:
        _stage("generating_video", language=lang)
        results.append(generate_language_video(
            facts, image_paths, story_director=director, translator=trans,
            target_language=lang, project_id=project_id,
            tts_provider=tts_provider, avatar_provider=avatar_provider, visual_provider=visual,
            precomputed_scenes=scenes,
            speaker=speaker, pace=resolved_pace, pitch=pitch, voice_gender=voice_gender,
        ))
    return results
