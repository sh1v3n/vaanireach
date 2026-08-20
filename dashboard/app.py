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
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Must happen before any provider is constructed — GeminiManager,
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
from providers.llm.gemini_client import GeminiAllKeysExhaustedError, GeminiManager  # noqa: E402
from providers.llm.gemini_provider import GeminiLLMProvider  # noqa: E402
from providers.tts.sarvam_tts_provider import SarvamTTSProvider  # noqa: E402
from providers.video.avatar_provider import AvatarFailoverProvider  # noqa: E402
from providers.visual.gemini_imagen_provider import GeminiImagenProvider  # noqa: E402
from rendering.adapters.moviepy_video_renderer import MoviePyVideoRenderer  # noqa: E402

# --------------------------------------------------------------------------- constants

TARGET_LANGUAGES: list[LanguageCode] = [LanguageCode.EN, LanguageCode.HI, LanguageCode.MR, LanguageCode.BN]
LANGUAGE_LABELS: dict[LanguageCode, str] = {
    LanguageCode.EN: "English",
    LanguageCode.HI: "हिन्दी (Hindi)",
    LanguageCode.MR: "मराठी (Marathi)",
    LanguageCode.BN: "বাংলা (Bengali)",
}

DEFAULT_AUDIENCE = "general public"
DEFAULT_DURATION_SECONDS = 60
BROLL_IMAGE_COUNT = 3
HOOK_SCENE_DURATION_SECONDS = 5.0

# A fixed, reused-across-projects presenter portrait. Generated once ever
# (LocalCache is keyed on this exact prompt string — see
# providers/visual/local_cache.py) rather than per notice, since the
# avatar is a consistent "on-screen presenter", not scheme-specific
# content.
SHARED_ASSET_PROJECT_ID = "vaanireach-shared-assets"
AVATAR_IMAGE_PROMPT = (
    "A friendly, professional Indian government outreach spokesperson: warm, approachable "
    "expression, looking directly at the camera, plain neutral studio background, upper-body "
    "portrait, soft even lighting, photorealistic, no text or logos in frame"
)

BROLL_PROMPT_TEMPLATE = """You are a visual director for a short Indian government outreach video. \
Based on the narration below, propose exactly {count} distinct B-roll image prompts — vivid, \
concrete, photographic scenes that visually support the narration's key points, suitable for a \
short-form vertical video aimed at: {audience}. Do NOT depict any real named individual, \
politician, or logo, and do NOT put any text/writing inside the image.

NARRATION:
{narration_text}

Return ONLY a JSON array of {count} strings, each one a self-contained image-generation prompt. \
No markdown fences, no commentary.
"""


# --------------------------------------------------------------------------- providers (cached for the process)

@dataclass
class Providers:
    llm: GeminiLLMProvider | None
    tts: SarvamTTSProvider
    avatar: AvatarFailoverProvider
    imagen: GeminiImagenProvider | None
    renderer: MoviePyVideoRenderer
    init_error: str | None


@st.cache_resource(show_spinner=False)
def get_providers() -> Providers:
    """Constructed once per process (not per rerun) — see module
    docstring. TTS/Avatar tolerate missing API keys internally (they fall
    back to edge-tts / the local Tier-3 clip respectively), but
    `GeminiManager` — shared between the LLM and Imagen providers so both
    draw from one key-rotation pool — raises if literally no Gemini key
    is configured anywhere, which is fatal for this dashboard (fact
    extraction/script generation have no fallback). That's caught here
    and surfaced as a normal `st.error`, not an unhandled crash."""
    try:
        gemini_manager = GeminiManager()
    except ValueError as exc:
        logger.error("get_providers: Gemini is not configured: %s", exc)
        return Providers(
            llm=None,
            tts=SarvamTTSProvider(),
            avatar=AvatarFailoverProvider(),
            imagen=None,
            renderer=MoviePyVideoRenderer(),
            init_error=str(exc),
        )

    return Providers(
        llm=GeminiLLMProvider(gemini_manager),
        tts=SarvamTTSProvider(),
        avatar=AvatarFailoverProvider(),
        imagen=GeminiImagenProvider(gemini_manager),
        renderer=MoviePyVideoRenderer(),
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
    """Fact extraction -> per-language script + claim generation ->
    verification -> one automatic regeneration pass for any language with
    blocking failures (the same verify -> regenerate -> verify loop
    documented in docs/workflow.md). Writes results into
    `st.session_state`; raises on a genuinely catastrophic failure
    (caller wraps this in try/except and shows `st.error`)."""
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

        for lang in TARGET_LANGUAGES:
            label = LANGUAGE_LABELS[lang]
            status.write(f"✍️ Drafting the {label} script…")
            script, lang_claims = providers.llm.generate_script_with_claims(
                source_facts, notice_text, lang, audience, duration_seconds, project_id=project.id,
            )

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

def generate_broll_prompts(manager: GeminiManager, narration_text: str, audience: str, *, count: int = BROLL_IMAGE_COUNT) -> list[str]:
    prompt = BROLL_PROMPT_TEMPLATE.format(count=count, narration_text=narration_text[:3000], audience=audience)
    prompts: list[str] = []
    try:
        raw = manager.generate_json(prompt, temperature=0.6)
        if isinstance(raw, list):
            prompts = [str(p).strip() for p in raw if str(p).strip()]
    except (GeminiAllKeysExhaustedError, ValueError) as exc:
        logger.warning("generate_broll_prompts: falling back to a generic prompt per slot (%s)", exc)

    while len(prompts) < count:
        prompts.append(f"A respectful, realistic photographic scene illustrating: {narration_text[:80].strip()}")
    return prompts[:count]


def get_avatar_source_image(providers: Providers) -> str:
    """The presenter portrait Hedra/D-ID animate for the hook clip.
    Generated through the same GeminiImagenProvider as the B-roll — its
    LocalCache means this only ever hits the network once across the
    dashboard's lifetime, not once per render."""
    assert providers.imagen is not None
    placeholder_scene = Scene(
        storyboard_id="shared-avatar-source", order_index=0, scene_type=SceneType.IMAGE_MOTION,
        narration_segment_text="avatar source portrait", duration_seconds=1.0,
    )
    asset = providers.imagen.generate_image(AVATAR_IMAGE_PROMPT, placeholder_scene, project_id=SHARED_ASSET_PROJECT_ID)
    return asset.storage_path  # type: ignore[return-value] - generate_image always sets storage_path on success


def run_render_pipeline(providers: Providers, project: Project, script: Script, audience: str, status) -> tuple:
    """TTS -> hook/body slice -> avatar hook clip -> B-roll prompts ->
    B-roll images -> MoviePy composite -> captions. Manually sequences
    Phases 2-4 in-process per the Phase 5 brief (no `WorkflowEngine`
    implementation exists yet). Raises on failure — the caller shows
    `st.error`; unlike the provider layer's own internal fallbacks, a
    broken final render has nothing further to degrade to."""
    assert providers.llm is not None and providers.imagen is not None
    storyboard_id = str(uuid.uuid4())

    status.write("🖼️ Preparing the presenter avatar image…")
    avatar_image_path = get_avatar_source_image(providers)

    status.write("🗣️ Synthesizing narration audio…")
    audio_asset = providers.tts.synthesize(
        script.narration_text, script.language, project_id=project.id, script_id=script.id,
    )

    status.write("✂️ Slicing hook / body audio…")
    hook_audio_path, body_audio_path = providers.tts.process_and_slice_audio(audio_asset.storage_path)
    if body_audio_path is None:
        raise RuntimeError(
            "The narration is too short to split into a hook clip and a B-roll body track — "
            "increase the target duration in the sidebar and try again."
        )

    status.write("🎭 Generating the talking-avatar hook clip…")
    hook_scene = Scene(
        storyboard_id=storyboard_id, order_index=0, scene_type=SceneType.AVATAR,
        narration_segment_text=script.narration_text[:300], duration_seconds=HOOK_SCENE_DURATION_SECONDS,
    )
    avatar_asset = providers.avatar.generate_avatar_hook(
        avatar_image_path, hook_audio_path, project_id=project.id, scene_id=hook_scene.id,
        text_prompt=script.narration_text[:300],
    )

    status.write("🎨 Drafting B-roll visual prompts…")
    broll_prompts = generate_broll_prompts(providers.llm.manager, script.narration_text, audience)

    broll_image_paths: list[str] = []
    for i, prompt in enumerate(broll_prompts):
        status.write(f"🖌️ Generating B-roll image {i + 1}/{len(broll_prompts)}…")
        broll_scene = Scene(
            storyboard_id=storyboard_id, order_index=i + 1, scene_type=SceneType.IMAGE_MOTION,
            narration_segment_text=prompt, duration_seconds=1.0, visual_prompt=prompt,
        )
        image_asset = providers.imagen.generate_image(prompt, broll_scene, project_id=project.id)
        broll_image_paths.append(image_asset.storage_path)  # type: ignore[arg-type]

    status.write("🎬 Stitching the final video (avatar hook + Ken Burns B-roll + captions)…")
    video_asset = providers.renderer.compose_final_video(
        avatar_asset.storage_path,  # type: ignore[arg-type]
        broll_image_paths,
        body_audio_path,
        project_id=project.id,
        storyboard_id=storyboard_id,
        language=script.language,
        captions_text=script.narration_text,
        output_name=f"{project.id}_{script.language.value}",
    )

    status.write("📝 Exporting captions…")
    captions_srt = providers.renderer.export_captions(script, None, "srt")

    return video_asset, captions_srt


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
            st.error(f"⚠️ Gemini is not configured: {providers.init_error}")

        with st.form("ingestion_form"):
            text = st.text_area(
                "Paste the official English notice",
                height=240,
                value=st.session_state.notice_text,
                placeholder="Paste the scheme/notice text here…",
            )
            uploaded = st.file_uploader("…or upload a .txt notice", type=["txt"])
            audience = st.text_input("Audience", value=DEFAULT_AUDIENCE)
            duration = st.slider("Target duration (seconds)", 30, 120, DEFAULT_DURATION_SECONDS, step=5)
            submitted = st.form_submit_button("Extract & Draft Scripts", type="primary", use_container_width=True)

        if not submitted:
            return None

        notice_text = uploaded.read().decode("utf-8", errors="replace").strip() if uploaded is not None else text.strip()
        if not notice_text:
            st.error("Paste or upload some notice text first.")
            return None
        if providers.llm is None:
            st.error("Gemini is not configured — set GEMINI_API_KEYS in .env and restart.")
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
    lang = st.selectbox("Language to render", langs, format_func=lambda l: LANGUAGE_LABELS[l], key="render_language")

    results = st.session_state.verification.get(lang, [])
    blocking = [r for r in results if r.is_blocking]
    override = True
    if blocking:
        st.warning(
            f"⚠️ {len(blocking)} unresolved blocking verification issue(s) for {LANGUAGE_LABELS[lang]}. "
            "Resolve them in Step 1 (or override below) before rendering."
        )
        override = st.checkbox("Render anyway despite unresolved issues", value=False, key=f"override_{lang.value}")

    disabled = bool(blocking) and not override
    submitted = st.button(
        "🎬 Approve & Render Video", type="primary", use_container_width=True, disabled=disabled,
    )

    if not submitted:
        return

    script = scripts[lang]
    try:
        with st.status(f"Rendering the {LANGUAGE_LABELS[lang]} video…", expanded=True) as status:
            video_asset, captions_srt = run_render_pipeline(
                providers, st.session_state.project, script, DEFAULT_AUDIENCE, status,
            )
            status.update(label="Render complete.", state="complete")
        st.session_state.rendered[lang] = {"video": video_asset, "captions_srt": captions_srt}
        st.success(f"✅ {LANGUAGE_LABELS[lang]} video rendered.")
    except Exception as exc:  # noqa: BLE001 - catastrophic render failure, shown to the officer per the brief
        logger.exception("run_render_pipeline failed for language=%s", lang.value)
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
        except GeminiAllKeysExhaustedError as exc:
            st.error(f"Gemini API key pool exhausted: {exc}")
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
