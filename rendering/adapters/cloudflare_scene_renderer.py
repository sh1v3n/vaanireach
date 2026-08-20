"""CloudflareSceneRenderer — SceneRenderer implementation that produces a
contextually-appropriate PHOTOGRAPH per scene (via CloudflareVisualProvider)
instead of a local HTML/CSS card. Built at the user's explicit request:
the video's background should match the document's actual subject matter
(e.g. a government-scheme notice -> the issuing office's exterior,
transitioning to interior/service-counter shots as the story moves from
context to action) rather than generic icon cards.

Prompt strategy is role-based and deterministic (no LLM involved, so it
works even with Gemini fully exhausted): HOOK/CONTEXT/CLOSING share an
"establishing exterior shot" family for bookend visual continuity —
directly the pattern in the user's own example (parliament exterior,
returning to it at the close) — while the middle roles get scene-specific
interior/detail shots. Every prompt explicitly steers away from
legible text/signage, a well-known failure mode of current image models
(confirmed empirically: an unguarded prompt rendered garbled signage
text on a generated building).

Per the Phase 2 plan's own principle (visual generation consumes facts,
never becomes a source of facts — core/interfaces/visual_provider.py),
these prompts are illustrative interpretations of the scene's role and
content, not claims subject to the same digit/date/name grounding checks
DeterministicFactVerifier applies to narration.
"""
from __future__ import annotations

import logging

from core.interfaces.scene_renderer import SceneRenderer
from core.models.enums import NarrativeRole, SceneType
from core.models.media import MediaAsset
from core.models.storyboard import Scene
from providers.llm.groq_client import GroqAllKeysExhaustedError, GroqManager
from providers.visual.cloudflare_provider import CloudflareVisualProvider

logger = logging.getLogger("vaanireach.rendering.cloudflare_scene_renderer")

_STYLE_SUFFIX = "photorealistic, natural lighting, respectful documentary style, no text, no signage, no watermark"

# HOOK/CONTEXT/CLOSING share one "establishing shot" family so the video
# opens and closes on visually continuous material — the exterior shot
# the story returns to, per the user's parliament-building example.
_ESTABLISHING_SHOT = f"Exterior of a formal Indian district government office building, {_STYLE_SUFFIX}"

_ROLE_PROMPTS: dict[NarrativeRole, str] = {
    NarrativeRole.HOOK: _ESTABLISHING_SHOT,
    NarrativeRole.CONTEXT: _ESTABLISHING_SHOT,
    NarrativeRole.PROBLEM: f"A farmer looking at farmland with a concerned expression, overcast light, {_STYLE_SUFFIX}",
    NarrativeRole.ANNOUNCEMENT: f"Interior of a formal government assembly or notice-board hall, "
                                 f"rows of officials seated, {_STYLE_SUFFIX}",
    NarrativeRole.BENEFIT: f"An Indian farmer receiving a document or assistance from a government official "
                            f"across a desk, warm and respectful tone, {_STYLE_SUFFIX}",
    NarrativeRole.ELIGIBILITY: f"A government official reviewing paperwork with a farmer, pointing at a "
                                f"checklist, {_STYLE_SUFFIX}",
    NarrativeRole.HOW_TO: f"Interior of a busy Common Service Centre, people registering at computer "
                           f"counters, {_STYLE_SUFFIX}",
    NarrativeRole.DEADLINE: f"A desk calendar and hourglass on a wooden office desk, warm afternoon "
                             f"light, {_STYLE_SUFFIX}",
    NarrativeRole.URGENCY: f"A clock on an office wall above a busy service counter, sense of motion, "
                            f"{_STYLE_SUFFIX}",
    NarrativeRole.CTA: f"A farmer handing a completed application form to a clerk at a service counter, "
                        f"{_STYLE_SUFFIX}",
    NarrativeRole.CLOSING: _ESTABLISHING_SHOT,
}


def build_scene_image_prompt(scene: Scene) -> str:
    """Deterministic, role-based photographic prompt for one scene — no
    LLM call, works even with every text-generation vendor exhausted."""
    return _ROLE_PROMPTS.get(
        scene.narrative_role,
        f"A respectful, realistic documentary photograph illustrating: {scene.narration_segment_text[:100]}, "
        f"{_STYLE_SUFFIX}",
    )


_FACT_AWARE_PROMPT_TEMPLATE = """You are a visual director for a short Indian government-notice \
outreach video. For each numbered scene below, write ONE short B-roll image-generation prompt \
(SUBJECT AND SETTING ONLY, at most 15 words) that visually matches that scene's actual subject \
matter — e.g. a tax/finance notice should show a tax office, a bank counter, someone reviewing a \
tax form; an agricultural scheme should show farmers/farmland; a health scheme should show a \
clinic. Match the REAL content, not a generic default.

Do NOT include lighting/style/quality words (photorealistic, natural lighting, documentary style,
etc.) — those are added separately afterward. Do NOT depict any real named individual, politician, \
brand, or legible text/signage. Keep every prompt terse: subject + setting, nothing else.

Scenes (role: narration):
{scenes_block}

Return ONLY a JSON array of exactly {count} short strings (max 15 words each), one per scene in \
the same order. No markdown fences, no commentary.
"""


def generate_fact_aware_image_prompts(
    scenes: list[Scene], *, groq_manager: GroqManager | None = None,
) -> list[str] | None:
    """One batched LLM call drafts a photorealistic B-roll prompt per
    scene, grounded in that scene's real narration (which itself only
    ever contains verbatim fact values or static connective phrasing —
    see template_story_director.py's fact-invention guard) — so a tax
    notice gets tax-office imagery instead of the static per-role
    templates' hardcoded farmer/agriculture scenes.

    Returns None (caller falls back to build_scene_image_prompt per
    scene) on ANY failure — a Groq outage, a malformed response, a
    count mismatch. This is a quality upgrade, never a hard dependency,
    matching this codebase's "always have a deterministic fallback"
    pattern (see TemplateStoryDirector, AvatarFailoverProvider)."""
    if not scenes:
        return None
    manager = groq_manager or GroqManager()
    scenes_block = "\n".join(
        f"{i + 1}. [{s.narrative_role.value}] {s.narration_segment_text}" for i, s in enumerate(scenes)
    )
    prompt = _FACT_AWARE_PROMPT_TEMPLATE.format(scenes_block=scenes_block, count=len(scenes))

    try:
        raw = manager.generate_json(prompt, temperature=0.6)
    except (GroqAllKeysExhaustedError, ValueError) as exc:
        logger.warning("generate_fact_aware_image_prompts: falling back to static per-role prompts (%s)", exc)
        return None

    if not isinstance(raw, list) or len(raw) != len(scenes):
        logger.warning(
            "generate_fact_aware_image_prompts: expected %d prompts, got %r — falling back",
            len(scenes), raw,
        )
        return None

    prompts = [str(p).strip() for p in raw]
    if any(not p for p in prompts):
        logger.warning("generate_fact_aware_image_prompts: empty prompt(s) in response — falling back")
        return None
    return [f"{p}, {_STYLE_SUFFIX}" for p in prompts]


def render_scene_images(
    scenes: list[Scene],
    visual_provider: CloudflareVisualProvider,
    *,
    project_id: str,
    groq_manager: GroqManager | None = None,
) -> list[str]:
    """Renders one B-roll image per scene, ONCE (shared across every
    language downstream, per multilingual_video.py's language-
    independence principle) — fact-aware prompts (real LLM call,
    grounded in the document's actual content) when available, falling
    back to the static per-role templates per scene otherwise."""
    fact_aware_prompts = generate_fact_aware_image_prompts(scenes, groq_manager=groq_manager)
    image_paths: list[str] = []
    for i, scene in enumerate(scenes):
        prompt = fact_aware_prompts[i] if fact_aware_prompts is not None else build_scene_image_prompt(scene)
        asset = visual_provider.generate_image(prompt, scene, project_id=project_id)
        image_paths.append(asset.storage_path)
    return image_paths


class CloudflareSceneRenderer(SceneRenderer):
    def __init__(self, visual_provider: CloudflareVisualProvider | None = None, *, project_id: str = "") -> None:
        self._provider = visual_provider or CloudflareVisualProvider()
        self._project_id = project_id

    def supports(self, scene_type: SceneType) -> bool:
        return True  # a contextual photo is a valid background for any scene_type

    def render_scene(self, scene: Scene) -> MediaAsset:
        prompt = build_scene_image_prompt(scene)
        return self._provider.generate_image(prompt, scene, project_id=self._project_id)
