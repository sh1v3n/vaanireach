"""VaaniReach — Officer Review Dashboard (Streamlit).

The Phase 0 "Human Review Dashboard" design (docs/architecture.md): an
officer reviews the Source (notice text, facts), Generated Content
(multilingual scripts, storyboard, final video), and Verification
(verified/contradicted/unverified claims with source references), then
takes a Final Action. **Publication is never automatic** — this dashboard
renders a preview video for review; nothing here uploads or publishes it
anywhere.

No `WorkflowEngine` implementation exists yet (see
`core/interfaces/orchestrator.py`), so per the Phase 5 brief this module
IS the orchestrator: it instantiates every concrete provider from Phases
1-4 directly and sequences them in-process. No HTTP calls to the FastAPI
backend are made — `backend/` and this dashboard are two independent
front-ends over the same `core`/`providers`/`rendering` packages.

Session-state discipline: Streamlit reruns this entire script top-to-bottom
on every interaction (a button click, a tab switch, a slider drag). The
expensive pipeline calls — fact extraction, per-language script
generation/verification, and the render pipeline — are only ever invoked
on the specific rerun where their triggering `st.form_submit_button()` /
`st.button()` returns True; every other rerun reads already-computed
results back out of `st.session_state`. Provider instances themselves
(and the API key rotation state they hold) are cached for the life of the
process via `st.cache_resource`, so they're constructed once, not on every
rerun.
"""
from __future__ import annotations

import logging
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Must happen before any provider is constructed — GroqManager,
# SarvamTTSManager, HedraAvatarManager, and DIDAvatarManager all read
# their API keys from os.environ at __init__ time.
load_dotenv()

# Make the repo root importable regardless of the CWD `streamlit run` is
# invoked from (it only auto-adds this file's own directory, not the repo
# root two levels up).
_ROOT_DIR = Path(__file__).resolve().parent.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

# So the provider-layer's own logger.warning/.error calls (fallback-tier
# notices, key-rotation events, etc.) are visible in the terminal running
# `streamlit run` — Streamlit does not configure logging itself.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("vaanireach.dashboard")

import streamlit as st  # noqa: E402 - after load_dotenv()/sys.path setup, see module docstring

from core.models import (  # noqa: E402
    DocumentPage,
    LanguageCode,
    Project,
    Scene,
    Script,
    SceneType,
    VerificationStatus,
)
from providers.llm.groq_client import GroqAllKeysExhaustedError, GroqManager  # noqa: E402
from providers.llm.groq_provider import GroqLLMProvider  # noqa: E402
from providers.tts.sarvam_tts_provider import SarvamTTSProvider  # noqa: E402
from providers.video.avatar_provider import AvatarFailoverProvider  # noqa: E402
from providers.visual.cloudflare_flux_provider import CloudflareFluxVisualProvider  # noqa: E402
from rendering.adapters.ffmpeg_video_renderer import FfmpegVideoRenderer  # noqa: E402

# --------------------------------------------------------------------------- constants

# Bengali dropped 2026-08-20, temporarily: Step 1 runs one extraction +
# script-generation + verification pass per target language, sequentially,
# all against the same shared Groq TPM budget (see groq_client.py's module
# docstring) — even with 3 keys in the pool, a 4th language's worth of
# calls was enough to exhaust it. Re-add LanguageCode.BN here (and its
# label below) once the key pool has more headroom.
TARGET_LANGUAGES: list[LanguageCode] = [LanguageCode.EN, LanguageCode.HI, LanguageCode.MR]
LANGUAGE_LABELS: dict[LanguageCode, str] = {
    LanguageCode.EN: "English",
    LanguageCode.HI: "हिन्दी (Hindi)",
    LanguageCode.MR: "मराठी (Marathi)",
    LanguageCode.BN: "বাংলা (Bengali)",  # kept for when BN is re-added to TARGET_LANGUAGES
}

DEFAULT_AUDIENCE = "general public"
# Capped to a short-form 30-40s so the whole video (hook + B-roll body)
# stays watchable in one sitting — SCRIPT_GENERATION_PROMPT's target_words
# (= duration * 2.3, see groq_provider.py) keeps the narration itself
# summarized to fit, rather than writing a longer script and truncating it
# after the fact.
MIN_DURATION_SECONDS = 30
MAX_DURATION_SECONDS = 40
DEFAULT_DURATION_SECONDS = 35
BROLL_IMAGE_COUNT = 3
HOOK_SCENE_DURATION_SECONDS = 5.0

# Ready-made presenter headshots (real, pre-shot photos) — used directly
# as the Hedra/D-ID animation source instead of an AI-generated portrait.
# Faster (no generation call at all) and gives a consistent, real-looking
# on-screen presenter rather than a fresh AI face every time. These are
# transparent-background PNGs (person cutouts, confirmed via their alpha
# channel — not genuinely white as they first appear in a plain viewer,
# which composites transparency onto white by default) — flattened onto
# PRESENTER_BACKGROUND_RGB before use, since Hedra/D-ID need a fully
# opaque source image and how each vendor would otherwise handle raw
# alpha transparency is unpredictable.
AVATAR_DIR = _ROOT_DIR / "avatar"
PRESENTER_IMAGE_PATHS: dict[str, Path] = {
    "Male": AVATAR_DIR / "avatar_male.png",
    "Female": AVATAR_DIR / "avatar_female.png",
}
DEFAULT_PRESENTER = "Female"
PRESENTER_BACKGROUND_RGB = (245, 245, 245)  # soft neutral studio white

BROLL_PROMPT_TEMPLATE = """You are a visual director for a short Indian government outreach video \
told as a brief STORY, not a list of facts — the same {count} B-roll images should feel like one \
coherent visual sequence (consistent setting/tone/time-of-day where plausible), each one advancing \
the narration's story beat by beat, not just illustrating an isolated fact in isolation.

Based on the narration below, propose exactly {count} distinct B-roll image prompts — vivid, \
concrete, photographic scenes that visually support the narration's key points, suitable for a \
short-form vertical video aimed at: {audience}. Do NOT depict any real named individual, \
politician, or logo, and do NOT put any text/writing inside the image.

NARRATION:
{narration_text}

Return ONLY a JSON array of {count} strings, in the same order the story should unfold, each one a \
self-contained image-generation prompt. No markdown fences, no commentary.
"""


# --------------------------------------------------------------------------- providers (cached for the process)

@dataclass
class Providers:
    llm: GroqLLMProvider | None
    tts: SarvamTTSProvider
    avatar: AvatarFailoverProvider
    visual: CloudflareFluxVisualProvider
    renderer: FfmpegVideoRenderer
    init_error: str | None


@st.cache_resource(show_spinner=False)
def get_providers() -> Providers:
    """Constructed once per process (not per rerun) — see module
    docstring. TTS/Avatar/Visual all tolerate missing/unavailable
    providers internally (they fall back to edge-tts / the local Tier-3
    clip / a local placeholder card respectively), but `GroqManager`
    raises if literally no Groq key is configured anywhere, which is
    fatal for this dashboard (fact extraction/script generation have no
    fallback). That's caught here and surfaced as a normal `st.error`,
    not an unhandled crash.

    The LLM backend (fact extraction, script generation, translation,
    semantic verification) is Groq (`GroqLLMProvider`,
    `openai/gpt-oss-120b`) — not Gemini. Gemini's free-tier daily quota
    (20 requests/day/key/model) proved far too tight for this pipeline's
    real usage: all 3 configured Gemini keys were repeatedly exhausted
    during live testing, blocking fact extraction entirely with no
    fallback. Groq's free tier is dramatically more generous (confirmed
    live: 1000 requests / 8000 tokens per short window) and its
    LPU-based inference is consistently sub-second. `GeminiLLMProvider`
    (providers/llm/gemini_provider.py) is kept in the codebase — correct,
    working — but no longer wired in. See
    providers/llm/groq_client.py's module docstring for the full
    rationale and model-choice verification.

    B-roll/avatar-source image generation is Cloudflare Workers AI's
    `@cf/black-forest-labs/flux-1-schnell` (`CloudflareFluxVisualProvider`)
    — the project's permanent image-generation provider: fast (a
    distilled few-step model, Cloudflare's edge network), reliable, and
    noticeably higher quality than every prior candidate in testing.
    Three prior providers were tried and dropped before landing here,
    each for a billing/quota wall: Google Imagen 3 requires a
    billing-enabled Cloud project even on free-tier Gemini keys, Hugging
    Face's free `hf-inference` tier (and Together AI, one of the
    backends its router can pick) started gating image generation behind
    billing/deposit, and Pollinations.ai's free public queue proved
    fine for a demo but wasn't the permanent answer. See
    providers/visual/cloudflare_flux_provider.py's module docstring."""
    try:
        groq_manager = GroqManager()
    except ValueError as exc:
        logger.error("get_providers: Groq is not configured: %s", exc)
        return Providers(
            llm=None,
            tts=SarvamTTSProvider(),
            avatar=AvatarFailoverProvider(),
            visual=CloudflareFluxVisualProvider(),
            renderer=FfmpegVideoRenderer(),
            init_error=str(exc),
        )

    return Providers(
        llm=GroqLLMProvider(groq_manager),
        tts=SarvamTTSProvider(),
        avatar=AvatarFailoverProvider(),
        visual=CloudflareFluxVisualProvider(),
        renderer=FfmpegVideoRenderer(),
        init_error=None,
    )


# --------------------------------------------------------------------------- session state

def _init_session_state() -> None:
    defaults = {
        "project": None,  # Project | None
        "notice_text": "",
        "document_id": None,  # str | None
        "source_facts": [],  # list[SourceFact]
        "scripts": {},  # dict[LanguageCode, Script]
        "claims": {},  # dict[LanguageCode, list[Claim]]
        "verification": {},  # dict[LanguageCode, list[VerificationResult]]
        "rendered": {},  # dict[LanguageCode, {"video": VideoAsset, "captions_srt": str}]
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _derive_project_name(notice_text: str) -> str:
    first_line = next((line.strip() for line in notice_text.splitlines() if line.strip()), "")
    return (first_line[:80] or "Untitled Outreach Notice").rstrip(".:,;")


# --------------------------------------------------------------------------- Step 0: ingestion pipeline

def run_extraction_and_drafting(providers: Providers, notice_text: str, audience: str, duration_seconds: int) -> None:
    """Fact extraction -> ONE combined multi-language script + claim
    generation call -> per-language verification -> one automatic
    regeneration pass for any language with blocking failures (the same
    verify -> regenerate -> verify loop documented in docs/workflow.md).
    Writes results into `st.session_state`; raises on a genuinely
    catastrophic failure (caller wraps this in try/except and shows
    `st.error`).

    Script generation uses `generate_scripts_with_claims_multi()` (one
    Groq call for every TARGET_LANGUAGES entry) rather than looping
    `generate_script_with_claims()` per language — see that method's
    docstring in groq_provider.py and providers/llm/README.md's
    "Reducing Groq TPM usage" section: the fact ledger + document context
    + prompt template were being resent in full once per language,
    competing for the same shared Groq TPM budget. Verification stays
    per-language (each language's claims are already batched into one
    verify_batch() call each, and only the languages with actual blocking
    issues pay for a regeneration pass, so there's no equivalent
    redundancy to remove there)."""
    assert providers.llm is not None  # guarded by the caller before this is ever invoked

    project = Project(name=_derive_project_name(notice_text), target_languages=TARGET_LANGUAGES)
    document_id = str(uuid.uuid4())
    pages = [DocumentPage(document_id=document_id, page_number=1, raw_text=notice_text)]

    scripts: dict[LanguageCode, Script] = {}
    claims_by_lang: dict = {}
    verification_by_lang: dict = {}

    with st.status("Running fact extraction & multilingual drafting…", expanded=True) as status:
        status.write("🔎 Extracting facts from the notice…")
        source_facts = providers.llm.extract_facts(document_id, pages, project_id=project.id)
        status.write(f"✅ Extracted {len(source_facts)} fact(s).")

        if not source_facts:
            status.update(label="No facts could be extracted from the supplied text.", state="error")
            return

        all_labels = " + ".join(LANGUAGE_LABELS[lang] for lang in TARGET_LANGUAGES)
        status.write(f"✍️ Drafting {all_labels} scripts in one combined call…")
        scripts_and_claims = providers.llm.generate_scripts_with_claims_multi(
            source_facts, notice_text, TARGET_LANGUAGES, audience, duration_seconds, project_id=project.id,
        )

        for lang in TARGET_LANGUAGES:
            label = LANGUAGE_LABELS[lang]
            script, lang_claims = scripts_and_claims[lang]

            status.write(f"🔍 Verifying {label} claims…")
            results = providers.llm.verify_batch(lang_claims, source_facts) if lang_claims else []

            blocking = [r for r in results if r.is_blocking]
            if blocking:
                status.write(f"⚠️ {label}: {len(blocking)} blocking issue(s) found — regenerating once…")
                script, lang_claims = providers.llm.regenerate_script_with_claims(script, results)
                results = providers.llm.verify_batch(lang_claims, source_facts) if lang_claims else []
                still_blocking = sum(1 for r in results if r.is_blocking)
                status.write(
                    f"↩️ {label}: regenerated — {still_blocking} blocking issue(s) remain"
                    if still_blocking else f"✅ {label}: regeneration resolved all blocking issues."
                )

            scripts[lang] = script
            claims_by_lang[lang] = lang_claims
            verification_by_lang[lang] = results
            verified_count = sum(1 for r in results if r.status == VerificationStatus.VERIFIED)
            status.write(f"✅ {label} ready — {len(lang_claims)} claim(s), {verified_count} verified.")

        status.update(label="Extraction & drafting complete.", state="complete")

    st.session_state.project = project
    st.session_state.document_id = document_id
    st.session_state.source_facts = source_facts
    st.session_state.scripts = scripts
    st.session_state.claims = claims_by_lang
    st.session_state.verification = verification_by_lang
    st.session_state.rendered = {}  # any prior render is now stale


def regenerate_language(providers: Providers, lang: LanguageCode) -> None:
    """Manual, officer-triggered re-run of the regenerate -> verify step
    for one language (beyond the single automatic pass
    `run_extraction_and_drafting` already does) — the dashboard's
    `REGENERATE` action from docs/architecture.md's Human Review
    Dashboard design."""
    assert providers.llm is not None
    script = st.session_state.scripts[lang]
    results = st.session_state.verification.get(lang, [])
    new_script, new_claims = providers.llm.regenerate_script_with_claims(script, results)
    new_results = providers.llm.verify_batch(new_claims, st.session_state.source_facts) if new_claims else []

    st.session_state.scripts[lang] = new_script
    st.session_state.claims[lang] = new_claims
    st.session_state.verification[lang] = new_results
    st.session_state.rendered.pop(lang, None)  # a prior render for this language is now stale


# --------------------------------------------------------------------------- Step 2: render pipeline

def generate_broll_prompts(manager: GroqManager, narration_text: str, audience: str, *, count: int = BROLL_IMAGE_COUNT) -> list[str]:
    prompt = BROLL_PROMPT_TEMPLATE.format(count=count, narration_text=narration_text[:3000], audience=audience)
    prompts: list[str] = []
    try:
        raw = manager.generate_json(prompt, temperature=0.6)
        if isinstance(raw, list):
            prompts = [str(p).strip() for p in raw if str(p).strip()]
    except (GroqAllKeysExhaustedError, ValueError) as exc:
        logger.warning("generate_broll_prompts: falling back to a generic prompt per slot (%s)", exc)

    while len(prompts) < count:
        prompts.append(f"A respectful, realistic photographic scene illustrating: {narration_text[:80].strip()}")
    return prompts[:count]


def get_presenter_image_path(presenter: str) -> str:
    """Ready-made headshot (a real, pre-shot photo) used directly as the
    Hedra/D-ID animation source for the given presenter — no AI image
    generation call at all, which is both strictly faster (one fewer
    network round trip on the critical path) and gives a consistent,
    real-looking on-screen presenter instead of a fresh AI-generated
    face every render. Flattened onto a neutral background and cached as
    a JPEG (see module docstring re: these being transparent PNGs) — the
    flatten only ever runs once per photo, not once per render."""
    src_path = PRESENTER_IMAGE_PATHS.get(presenter, PRESENTER_IMAGE_PATHS[DEFAULT_PRESENTER])
    if not src_path.exists():
        raise FileNotFoundError(
            f"Presenter image not found at {src_path} — expected ready-made headshots under {AVATAR_DIR}/"
        )

    flattened_path = src_path.with_suffix(".flattened.jpg")
    if flattened_path.exists():
        return str(flattened_path)

    from PIL import Image

    source = Image.open(src_path).convert("RGBA")
    background = Image.new("RGB", source.size, PRESENTER_BACKGROUND_RGB)
    background.paste(source, mask=source.split()[3])
    background.save(flattened_path, format="JPEG", quality=95)
    return str(flattened_path)


def _produce_avatar_hook(
    providers: Providers, avatar_image_path: str, script: Script, project: Project, storyboard_id: str,
) -> tuple:
    """TTS -> hook/body slice -> avatar hook clip. Runs entirely inside a
    worker thread (see run_multi_language_render_pipeline) in parallel
    with _produce_broll(), since neither branch depends on the other's
    output. Called once per render — against the reference language only,
    see run_multi_language_render_pipeline — not once per selected
    language. Deliberately never calls `status.write()`/any `st.*`
    function — Streamlit's UI calls are not safe to make off the main
    script-run thread, so all progress messages are emitted by the
    caller, only before/after the parallel section."""
    audio_asset = providers.tts.synthesize(
        script.narration_text, script.language, project_id=project.id, script_id=script.id,
    )
    hook_audio_path, body_audio_path = providers.tts.process_and_slice_audio(audio_asset.storage_path)
    if body_audio_path is None:
        raise RuntimeError(
            "The narration is too short to split into a hook clip and a B-roll body track — "
            "increase the target duration in the sidebar and try again."
        )

    hook_scene = Scene(
        storyboard_id=storyboard_id, order_index=0, scene_type=SceneType.AVATAR,
        narration_segment_text=script.narration_text[:300], duration_seconds=HOOK_SCENE_DURATION_SECONDS,
    )
    avatar_asset = providers.avatar.generate_avatar_hook(
        avatar_image_path, hook_audio_path, project_id=project.id, scene_id=hook_scene.id,
        text_prompt=script.narration_text[:300],
    )
    return avatar_asset, body_audio_path


def _produce_broll(providers: Providers, script: Script, audience: str, project: Project, storyboard_id: str) -> list[str]:
    """B-roll prompt drafting -> every image generated concurrently
    (each Cloudflare call is independent). Runs entirely inside a worker
    thread in parallel with _produce_avatar_hook() — see that function's
    docstring re: never touching Streamlit from here."""
    assert providers.llm is not None
    broll_prompts = generate_broll_prompts(providers.llm.manager, script.narration_text, audience)

    def _one(indexed_prompt: tuple[int, str]) -> str:
        i, prompt = indexed_prompt
        broll_scene = Scene(
            storyboard_id=storyboard_id, order_index=i + 1, scene_type=SceneType.IMAGE_MOTION,
            narration_segment_text=prompt, duration_seconds=1.0, visual_prompt=prompt,
        )
        asset = providers.visual.generate_image(prompt, broll_scene, project_id=project.id)
        return asset.storage_path  # type: ignore[return-value]

    with ThreadPoolExecutor(max_workers=len(broll_prompts)) as pool:
        return list(pool.map(_one, enumerate(broll_prompts)))


def run_multi_language_render_pipeline(
    providers: Providers,
    project: Project,
    scripts: dict[LanguageCode, Script],
    languages: list[LanguageCode],
    audience: str,
    presenter: str,
    status,
) -> dict[LanguageCode, tuple]:
    """Renders every selected language from ONE shared avatar hook clip +
    ONE shared B-roll image set, generated once against a single
    "reference" language's script (English when available, else the
    first selected language) — both are visual-only and don't actually
    depend on which language's audio ends up under them. Every other
    selected language then only needs its own TTS narration synthesized
    and swapped in (`compose_final_video`'s `hook_audio_path` param) —
    turning N full language renders into 1 avatar-animation call + 1
    B-roll image set + N cheap TTS/composite passes, instead of N of
    everything.

    Trade-off, deliberate and documented (see
    FfmpegVideoRenderer.compose_final_video's docstring): the avatar's
    lip movements are only phoneme-accurate for the reference language —
    every other language gets the same visual motion with its own audio
    swapped in underneath, "reporter over B-roll" style, which is also
    what the Tier-3 static fallback already looks like whenever
    Hedra/D-ID are unavailable.

    Manually sequences Phases 2-4 in-process per the Phase 5 brief (no
    `WorkflowEngine` implementation exists yet). Raises on failure — the
    caller shows `st.error`; unlike the provider layer's own internal
    fallbacks, a broken final render has nothing further to degrade to."""
    assert providers.llm is not None
    if not languages:
        raise ValueError("run_multi_language_render_pipeline: no languages selected")

    storyboard_id = str(uuid.uuid4())
    avatar_image_path = get_presenter_image_path(presenter)

    ref_lang = LanguageCode.EN if LanguageCode.EN in scripts else languages[0]
    ref_script = scripts[ref_lang]
    ref_label = LANGUAGE_LABELS[ref_lang]

    status.write(f"🎬 Generating the shared avatar hook + B-roll visuals once (from the {ref_label} script)…")
    with ThreadPoolExecutor(max_workers=2) as executor:
        avatar_future = executor.submit(
            _produce_avatar_hook, providers, avatar_image_path, ref_script, project, storyboard_id,
        )
        broll_future = executor.submit(_produce_broll, providers, ref_script, audience, project, storyboard_id)

        ref_avatar_asset, ref_body_audio_path = avatar_future.result()
        broll_image_paths = broll_future.result()

    results: dict[LanguageCode, tuple] = {}
    for lang in languages:
        label = LANGUAGE_LABELS[lang]
        script = scripts[lang]

        if lang == ref_lang:
            status.write(f"🖼️ Stitching {label} (the reference render — original lip-synced audio)…")
            hook_audio_override_path = None
            body_audio_path = ref_body_audio_path
        else:
            status.write(f"🔊 Synthesizing {label} narration and swapping it onto the shared video…")
            audio_asset = providers.tts.synthesize(
                script.narration_text, lang, project_id=project.id, script_id=script.id,
            )
            hook_audio_override_path, body_audio_path = providers.tts.process_and_slice_audio(audio_asset.storage_path)
            if body_audio_path is None:
                raise RuntimeError(
                    f"{label}: the narration is too short to split into a hook clip and a B-roll body "
                    "track — increase the target duration in the sidebar and try again."
                )
            status.write(f"🖼️ Stitching {label}…")

        video_asset = providers.renderer.compose_final_video(
            ref_avatar_asset.storage_path,  # type: ignore[arg-type]
            broll_image_paths,
            body_audio_path,
            project_id=project.id,
            storyboard_id=storyboard_id,
            language=lang,
            captions_text=script.narration_text,
            output_name=f"{project.id}_{lang.value}",
            hook_audio_path=hook_audio_override_path,
        )
        captions_srt = providers.renderer.export_captions(script, None, "srt")
        results[lang] = (video_asset, captions_srt)
        status.write(f"✅ {label} video ready.")

    return results


# --------------------------------------------------------------------------- UI: sidebar (ingestion)

def render_sidebar(providers: Providers) -> dict | None:
    """Returns a validated ingestion request dict on the specific rerun
    the form was submitted on, else None. The actual pipeline call stays
    OUT of this function (and out of the `with st.sidebar:` block) so its
    `st.status()` progress UI renders in the wide main area, not the
    narrow sidebar."""
    with st.sidebar:
        st.title("🇮🇳 VaaniReach - Multilingual Outreach")
        st.caption("Officer Review Dashboard")

        if providers.init_error:
            st.error(f"⚠️ Groq is not configured: {providers.init_error}")

        with st.form("ingestion_form"):
            text = st.text_area(
                "Paste the official English notice",
                height=240,
                value=st.session_state.notice_text,
                placeholder="Paste the scheme/notice text here…",
            )
            uploaded = st.file_uploader("…or upload a .txt notice", type=["txt"])
            audience = st.text_input("Audience", value=DEFAULT_AUDIENCE)
            duration = st.slider(
                "Target duration (seconds)",
                MIN_DURATION_SECONDS, MAX_DURATION_SECONDS, DEFAULT_DURATION_SECONDS, step=5,
            )
            submitted = st.form_submit_button("Extract & Draft Scripts", type="primary", use_container_width=True)

        if not submitted:
            return None

        notice_text = uploaded.read().decode("utf-8", errors="replace").strip() if uploaded is not None else text.strip()
        if not notice_text:
            st.error("Paste or upload some notice text first.")
            return None
        if providers.llm is None:
            st.error("Groq is not configured — set GROQ_API_KEYS in .env and restart.")
            return None

        return {"notice_text": notice_text, "audience": audience.strip() or DEFAULT_AUDIENCE, "duration": duration}


# --------------------------------------------------------------------------- UI: Step 1 (review & verify)

def render_step1(providers: Providers) -> None:
    st.header("Step 1 — Review & Verify")

    st.subheader("📒 Source Fact Ledger")
    facts = st.session_state.source_facts
    if not facts:
        st.info("No facts extracted yet.")
        return
    st.dataframe(
        [
            {
                "Type": f.fact_type.value,
                "Value": f.value,
                "Criticality": f.criticality.value,
                "Confidence": round(f.confidence, 2),
                "Source text": f.raw_text,
            }
            for f in facts
        ],
        use_container_width=True,
        hide_index=True,
    )

    langs = list(st.session_state.scripts.keys())
    if not langs:
        return

    tabs = st.tabs([LANGUAGE_LABELS[lang] for lang in langs])
    for tab, lang in zip(tabs, langs):
        with tab:
            script: Script = st.session_state.scripts[lang]
            claims = st.session_state.claims.get(lang, [])
            results = st.session_state.verification.get(lang, [])
            results_by_claim = {r.claim_id: r for r in results}

            st.text_area(
                "Narration script", value=script.narration_text, height=160,
                disabled=True, key=f"script_{lang.value}",
            )

            st.markdown("**Trust Layer — Claim Verification**")
            if not claims:
                st.info("No checkable claims were generated for this script.")
            for claim in claims:
                result = results_by_claim.get(claim.id)
                if result is None:
                    st.info(f"⏳ {claim.claim_text} — not yet verified")
                    continue
                message = f"**{claim.claim_text}**\n\n{result.explanation}"
                if result.status == VerificationStatus.VERIFIED:
                    st.success(message, icon="✅")
                elif result.is_blocking:
                    st.error(message, icon="🚨")
                else:
                    st.warning(message, icon="⚠️")

            blocking = [r for r in results if r.is_blocking]
            if blocking:
                if st.button(f"🔁 Regenerate {LANGUAGE_LABELS[lang]} script", key=f"regen_{lang.value}"):
                    try:
                        with st.spinner(f"Regenerating the {LANGUAGE_LABELS[lang]} script…"):
                            regenerate_language(providers, lang)
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001 - surfaced to the officer, not a crash
                        st.error(f"Regeneration failed: {exc}")


# --------------------------------------------------------------------------- UI: Step 2 (approve & render)

def render_step2(providers: Providers) -> None:
    scripts = st.session_state.scripts
    if not scripts:
        return

    st.header("Step 2 — Approve & Render")
    langs = list(scripts.keys())
    col_lang, col_presenter = st.columns(2)
    with col_lang:
        selected_langs = st.multiselect(
            "Languages to render",
            langs,
            default=langs[:1],
            format_func=lambda l: LANGUAGE_LABELS[l],
            key="render_languages",
            help=(
                "The avatar hook clip and B-roll images are generated once and reused across every "
                "language selected here — only the narration audio is swapped per language."
            ),
        )
    with col_presenter:
        presenter = st.selectbox("Presenter", list(PRESENTER_IMAGE_PATHS), key="render_presenter")

    blocking_by_lang = {
        lang: [r for r in st.session_state.verification.get(lang, []) if r.is_blocking]
        for lang in selected_langs
    }
    langs_with_blocking = [lang for lang, blocking in blocking_by_lang.items() if blocking]
    override = True
    if langs_with_blocking:
        names = ", ".join(LANGUAGE_LABELS[lang] for lang in langs_with_blocking)
        st.warning(f"⚠️ Unresolved blocking verification issue(s) for: {names}. Resolve in Step 1 (or override below).")
        override = st.checkbox("Render anyway despite unresolved issues", value=False, key="override_multi")

    disabled = not selected_langs or (bool(langs_with_blocking) and not override)
    submitted = st.button(
        "🎬 Approve & Render Video(s)", type="primary", use_container_width=True, disabled=disabled,
    )

    if not submitted:
        return

    try:
        label = " + ".join(LANGUAGE_LABELS[lang] for lang in selected_langs)
        with st.status(f"Rendering {label}…", expanded=True) as status:
            rendered = run_multi_language_render_pipeline(
                providers, st.session_state.project, scripts, selected_langs, DEFAULT_AUDIENCE, presenter, status,
            )
            status.update(label="Render complete.", state="complete")
        for lang, (video_asset, captions_srt) in rendered.items():
            st.session_state.rendered[lang] = {"video": video_asset, "captions_srt": captions_srt}
        st.success(f"✅ Rendered: {label}.")
    except Exception as exc:  # noqa: BLE001 - catastrophic render failure, shown to the officer per the brief
        logger.exception("run_multi_language_render_pipeline failed for languages=%r", [l.value for l in selected_langs])
        st.error(f"Rendering failed: {exc}")


# --------------------------------------------------------------------------- UI: Step 3 (output)

def render_output() -> None:
    rendered = st.session_state.rendered
    if not rendered:
        return

    st.header("Step 3 — Final Output")
    langs = list(rendered.keys())
    tabs = st.tabs([LANGUAGE_LABELS[lang] for lang in langs])
    for tab, lang in zip(tabs, langs):
        with tab:
            entry = rendered[lang]
            video_asset = entry["video"]
            video_path = Path(video_asset.storage_path_mp4)

            st.video(str(video_path))
            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    "⬇️ Download video (.mp4)", data=video_path.read_bytes(), file_name=video_path.name,
                    mime="video/mp4", use_container_width=True, key=f"dl_video_{lang.value}",
                )
            with col2:
                st.download_button(
                    "⬇️ Download captions (.srt)", data=entry["captions_srt"].encode("utf-8"),
                    file_name=f"{video_path.stem}.srt", mime="text/plain", use_container_width=True,
                    key=f"dl_srt_{lang.value}",
                )


# --------------------------------------------------------------------------- main

def main() -> None:
    st.set_page_config(page_title="VaaniReach", page_icon="🇮🇳", layout="wide", initial_sidebar_state="expanded")
    _init_session_state()
    providers = get_providers()

    request = render_sidebar(providers)
    if request is not None:
        try:
            run_extraction_and_drafting(providers, request["notice_text"], request["audience"], request["duration"])
        except GroqAllKeysExhaustedError as exc:
            st.error(f"Groq API key pool exhausted: {exc}")
        except Exception as exc:  # noqa: BLE001 - catastrophic pipeline failure, shown to the officer per the brief
            logger.exception("run_extraction_and_drafting failed")
            st.error(f"Extraction failed: {exc}")

    st.title("VaaniReach — Officer Review Dashboard")

    if st.session_state.project is None:
        st.info("👋 Paste an official notice in the sidebar and click **Extract & Draft Scripts** to begin.")
        return

    render_step1(providers)
    render_step2(providers)
    render_output()


if __name__ == "__main__":
    main()
