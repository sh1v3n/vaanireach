"""dynamic_narration — LLM-drafted narration text per scene, grounded in
that scene's own cited facts, replacing TemplateStoryDirector's hardcoded
per-role sentence templates (e.g. the "Farmers of X" bug: a fixed
audience assumption that had nothing to do with a tax/health/education
document's real content).

Same trusted pattern already proven live this session for B-roll image
prompts (rendering/adapters/cloudflare_scene_renderer.py's
generate_fact_aware_image_prompts): one batched Groq call, defensive
JSON-object-root unwrap, graceful fallback to the deterministic template
on ANY failure. This module adds one more safety layer on top, because
narration is the thing the fact-invention guard exists to protect: every
candidate line is independently re-verified against the real fact ledger
(DeterministicFactVerifier — the same engine that drives the pipeline's
"facts verified" / "blocking issues" metrics) before it's accepted. A
scene whose LLM line invents something not in its own cited facts reverts
to the original deterministic line for THAT SCENE ONLY — one bad line
never discards the whole batch.

Called once, in English, before translation (rendering/multilingual_video.py's
run_full_pipeline) — every language then translates from this same
dynamic narration, exactly like today's deterministic narration, so the
language-independence principle (StoryDirector runs once; only text
varies per language) is unchanged.
"""
from __future__ import annotations

import logging

from core.models.enums import VerificationStatus
from core.models.fact import SourceFact
from core.models.storyboard import Scene
from providers.llm.groq_client import GroqAllKeysExhaustedError, GroqManager
from providers.narrative.template_story_director import estimate_narration_duration
from providers.verification.deterministic_fact_verifier import DeterministicFactVerifier, claims_from_scenes

logger = logging.getLogger("vaanireach.providers.narrative.dynamic_narration")

_DYNAMIC_NARRATION_PROMPT_TEMPLATE = """You are a scriptwriter for a short Indian government-notice \
outreach video, writing plain, direct spoken-English narration. For each numbered scene below, write \
ONE short narration line (one or two natural spoken sentences) that:

- Uses ONLY the exact fact value(s) listed for that scene, copied verbatim — never paraphrase or \
alter a number, date, amount, name, URL, or phone number.
- NEVER invents who the audience is ("farmers", "citizens", "residents", "shopkeepers", etc.) unless \
a cited fact value itself names that audience. When in doubt, address the viewer directly as "you" \
instead of guessing a profession or group.
- NEVER adds any fact, statistic, or claim beyond the cited value(s) given for that scene.
- NEVER uses a relative time word ("today", "now", "soon", "currently", "hurry") — no verified \
current date is available to compare against.
- Matches the scene's story role (hook = grab attention using only the cited context; announcement \
= state the scheme/policy; benefit = what recipients get; eligibility = who qualifies, per the cited \
facts only; how_to = how to apply; deadline = when it closes).

The "current line" shown for each scene is today's fallback template text — match its tone and \
directness, but do not feel bound to its exact wording or its audience assumption if the cited facts \
don't support it.

Scenes:
{scenes_block}

Return ONLY a JSON object of the exact shape {{"narrations": [...]}}, where the array has exactly \
{count} strings, one per scene in the same order. No markdown fences, no commentary, no other keys.
"""


def _format_scene_line(index: int, scene: Scene, facts_by_id: dict[str, SourceFact]) -> str:
    cited_values = [facts_by_id[fid].value for fid in scene.source_fact_ids if fid in facts_by_id]
    cited_str = "; ".join(cited_values) if cited_values else "(no facts cited)"
    return (
        f'{index + 1}. [{scene.narrative_role.value}] cited facts: {cited_str} '
        f'| current line: "{scene.narration_segment_text}"'
    )


def generate_dynamic_narration(
    scenes: list[Scene],
    facts: list[SourceFact],
    *,
    project_id: str = "",
    groq_manager: GroqManager | None = None,
) -> list[Scene]:
    """Returns a new list of scenes — each with narration_segment_text
    (and a recomputed pre-TTS duration_seconds estimate) replaced by an
    LLM-drafted, per-scene-verified line where verification passed, or
    left exactly as given (TemplateStoryDirector's deterministic text)
    wherever the LLM call failed outright or a specific scene's line
    didn't verify. Never raises — this is a quality upgrade over the
    deterministic core, never a hard dependency, matching this
    codebase's established fallback pattern (TemplateStoryDirector,
    AvatarFailoverProvider, generate_fact_aware_image_prompts)."""
    if not scenes:
        return scenes

    manager = groq_manager or GroqManager()
    facts_by_id = {f.id: f for f in facts}
    scenes_block = "\n".join(_format_scene_line(i, s, facts_by_id) for i, s in enumerate(scenes))
    prompt = _DYNAMIC_NARRATION_PROMPT_TEMPLATE.format(scenes_block=scenes_block, count=len(scenes))

    try:
        raw = manager.generate_json(prompt, temperature=0.6)
    except (GroqAllKeysExhaustedError, ValueError) as exc:
        logger.warning(
            "generate_dynamic_narration: falling back to deterministic template narration for all "
            "scenes (%s)", exc,
        )
        return scenes

    # Groq's JSON mode validates against an object at the root — a bare
    # top-level array gets rejected even when well-formed (same gotcha
    # already fixed live for generate_fact_aware_image_prompts).
    if isinstance(raw, dict):
        raw = raw.get("narrations", [])

    if not isinstance(raw, list) or len(raw) != len(scenes):
        logger.warning(
            "generate_dynamic_narration: expected %d narration lines, got %r — falling back",
            len(scenes), raw,
        )
        return scenes

    candidate_lines = [str(line).strip() for line in raw]
    if any(not line for line in candidate_lines):
        logger.warning("generate_dynamic_narration: empty narration line(s) in response — falling back")
        return scenes

    candidates = [
        scene.model_copy(update={
            "narration_segment_text": line,
            "duration_seconds": estimate_narration_duration(line),
        })
        for scene, line in zip(scenes, candidate_lines)
    ]

    # Per-scene safety net: verify each candidate line against the real
    # fact ledger with the same engine the pipeline already trusts for
    # its "facts verified" / "blocking issues" metrics. A scene that
    # fails reverts to the original deterministic line — one invented
    # detail in one scene must never discard every other scene's
    # genuinely-improved narration.
    claims = claims_from_scenes(candidates, project_id=project_id)
    results = DeterministicFactVerifier().verify_batch(claims, facts)

    final_scenes: list[Scene] = []
    for original, candidate, result in zip(scenes, candidates, results):
        if result.status == VerificationStatus.VERIFIED:
            final_scenes.append(candidate)
        else:
            logger.warning(
                "generate_dynamic_narration: scene %s (role=%s) failed verification (%s) — "
                "reverting to deterministic narration. Rejected line: %r",
                original.id, original.narrative_role.value, result.status.value,
                candidate.narration_segment_text,
            )
            final_scenes.append(original)
    return final_scenes
