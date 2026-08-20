"""CloudflareSceneRenderer — generates a contextually-appropriate
photographic prompt per scene from its narrative_role (establishing shot
for HOOK/CONTEXT, interior/detail shots as the story progresses, per the
user's "parliament exterior -> inside a session" example) and renders it
via CloudflareVisualProvider. Requires CLOUDFLARE_ACCOUNT_ID/
CLOUDFLARE_API_TOKEN for the real-call test; skipped otherwise.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from core.interfaces.scene_renderer import SceneRenderer  # noqa: E402
from core.models.enums import GenerationStatus, MediaAssetType, NarrativeRole, SceneType  # noqa: E402
from core.models.media import MediaAsset  # noqa: E402
from core.models.narrative import VisualConcept  # noqa: E402
from core.models.storyboard import Scene  # noqa: E402
from providers.visual.local_cache import LocalCache  # noqa: E402
from rendering.adapters.cloudflare_scene_renderer import (  # noqa: E402
    _STYLE_SUFFIX, CloudflareSceneRenderer, build_scene_image_prompt, generate_fact_aware_image_prompts,
    render_scene_images,
)

_HAS_CREDS = bool(os.environ.get("CLOUDFLARE_ACCOUNT_ID")) and bool(os.environ.get("CLOUDFLARE_API_TOKEN"))


def _scene(role: NarrativeRole, narration: str = "test narration", elements=None) -> Scene:
    return Scene(
        storyboard_id="test", order_index=0, scene_type=SceneType.TEXT,
        narrative_role=role, narration_segment_text=narration, duration_seconds=3.0,
        visual_concept=VisualConcept(summary=narration, elements=elements or ["farmer_icon"], visual_beats=["a beat"]),
    )


@pytest.mark.parametrize("role", list(NarrativeRole))
def test_every_role_produces_a_nonempty_prompt(role):
    prompt = build_scene_image_prompt(_scene(role))
    assert prompt.strip() != ""


def test_prompt_never_requests_visible_text():
    """Known AI image-gen limitation: legible text/signage renders as
    garbled artifacts. Every prompt explicitly steers away from it."""
    for role in NarrativeRole:
        prompt = build_scene_image_prompt(_scene(role))
        assert "no text" in prompt.lower() or "no signage" in prompt.lower()


def test_hook_and_context_use_an_establishing_exterior_shot():
    hook_prompt = build_scene_image_prompt(_scene(NarrativeRole.HOOK))
    assert "exterior" in hook_prompt.lower() or "building" in hook_prompt.lower()


def test_closing_reuses_the_establishing_shot_style_for_continuity():
    """Bookend continuity: CLOSING should read like the same establishing
    shot family as HOOK/CONTEXT, not a random new scene."""
    hook_prompt = build_scene_image_prompt(_scene(NarrativeRole.HOOK)).lower()
    closing_prompt = build_scene_image_prompt(_scene(NarrativeRole.CLOSING)).lower()
    assert ("exterior" in closing_prompt or "building" in closing_prompt)


def test_renderer_supports_all_scene_types():
    renderer = CloudflareSceneRenderer()
    assert isinstance(renderer, SceneRenderer)
    for scene_type in SceneType:
        assert renderer.supports(scene_type) is True


@pytest.mark.skipif(not _HAS_CREDS, reason="CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN not set")
def test_renders_a_real_contextual_image(tmp_path):
    from providers.visual.cloudflare_provider import CloudflareVisualProvider

    provider = CloudflareVisualProvider(cache=LocalCache(tmp_path, extension="jpg"))
    renderer = CloudflareSceneRenderer(visual_provider=provider, project_id="test-project")
    scene = _scene(NarrativeRole.CONTEXT, narration="This announcement comes from the District Collectorate.")

    asset = renderer.render_scene(scene)
    assert asset.generation_status == GenerationStatus.COMPLETE
    assert asset.storage_path is not None
    path = Path(asset.storage_path)
    assert path.exists() and path.stat().st_size > 10_000

    from PIL import Image
    with Image.open(path) as img:
        img.verify()


class _FakeGroqManager:
    """No network — returns a scripted response or raises a scripted
    exception, so these tests never touch a real Groq quota."""

    def __init__(self, *, response=None, raises: Exception | None = None) -> None:
        self.response = response
        self.raises = raises
        self.calls: list[str] = []

    def generate_json(self, prompt, *, temperature=0.2, **kwargs):
        self.calls.append(prompt)
        if self.raises is not None:
            raise self.raises
        return self.response


class _FakeVisualProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []  # prompts passed to generate_image, in order

    def generate_image(self, prompt, scene, *, project_id):
        self.calls.append(prompt)
        return MediaAsset(
            project_id=project_id, scene_id=scene.id, asset_type=MediaAssetType.IMAGE,
            storage_path=f"/tmp/fake-{len(self.calls)}.jpg", provider_name="fake",
            generation_status=GenerationStatus.COMPLETE,
        )


def test_generate_fact_aware_image_prompts_unwraps_an_object_wrapped_response():
    """Regression guard for a real bug found live (2026-08-21): Groq's
    JSON mode validates against an object at the root — a bare top-level
    array gets rejected with json_validate_failed even when well-formed.
    The prompt now asks for {"prompts": [...]}; this must be unwrapped
    the same way groq_provider.py's extract_facts already unwraps
    {"facts": [...]}."""
    scenes = [_scene(NarrativeRole.BENEFIT, narration="Eligible taxpayers receive a rebate of ₹10,000.")]
    fake = _FakeGroqManager(response={"prompts": ["A person reviewing a tax form at a bank counter"]})
    result = generate_fact_aware_image_prompts(scenes, groq_manager=fake)
    assert result is not None
    assert len(result) == 1
    assert "tax form" in result[0]


def test_generate_fact_aware_image_prompts_returns_one_prompt_per_scene():
    scenes = [
        _scene(NarrativeRole.ANNOUNCEMENT, narration="Announcing the Income Tax Relief Scheme."),
        _scene(NarrativeRole.BENEFIT, narration="Eligible taxpayers receive a rebate of ₹10,000."),
    ]
    fake = _FakeGroqManager(response=["A tax office interior, an official at a desk", "A person reviewing a tax form at a bank counter"])
    result = generate_fact_aware_image_prompts(scenes, groq_manager=fake)
    assert result is not None
    assert len(result) == 2
    # the real narration text (not a generic farmer default) reached the LLM prompt
    assert "Income Tax Relief Scheme" in fake.calls[0]
    assert "₹10,000" in fake.calls[0]
    # every returned prompt still carries the same style suffix as the static templates
    assert result[0].endswith(_STYLE_SUFFIX)


def test_generate_fact_aware_image_prompts_falls_back_to_none_on_groq_exhaustion():
    from providers.llm.groq_client import GroqAllKeysExhaustedError

    scenes = [_scene(NarrativeRole.BENEFIT)]
    fake = _FakeGroqManager(raises=GroqAllKeysExhaustedError("all keys dead"))
    assert generate_fact_aware_image_prompts(scenes, groq_manager=fake) is None


def test_generate_fact_aware_image_prompts_falls_back_to_none_on_count_mismatch():
    scenes = [_scene(NarrativeRole.BENEFIT), _scene(NarrativeRole.ELIGIBILITY)]
    fake = _FakeGroqManager(response=["only one prompt"])  # 2 scenes, 1 prompt back
    assert generate_fact_aware_image_prompts(scenes, groq_manager=fake) is None


def test_generate_fact_aware_image_prompts_falls_back_to_none_on_empty_prompt():
    scenes = [_scene(NarrativeRole.BENEFIT)]
    fake = _FakeGroqManager(response=["   "])  # blank after strip
    assert generate_fact_aware_image_prompts(scenes, groq_manager=fake) is None


def test_render_scene_images_uses_fact_aware_prompts_when_available():
    scenes = [_scene(NarrativeRole.BENEFIT, narration="Eligible taxpayers receive a rebate of ₹10,000.")]
    fake_groq = _FakeGroqManager(response=["A person reviewing a tax form at a bank counter"])
    fake_visual = _FakeVisualProvider()

    image_paths = render_scene_images(scenes, fake_visual, project_id="test", groq_manager=fake_groq)

    assert len(image_paths) == 1
    assert "tax form" in fake_visual.calls[0]
    assert "farmer" not in fake_visual.calls[0].lower()  # not the static per-role default


def test_render_scene_images_falls_back_to_static_templates_when_groq_fails():
    from providers.llm.groq_client import GroqAllKeysExhaustedError

    scenes = [_scene(NarrativeRole.BENEFIT, narration="Eligible taxpayers receive a rebate of ₹10,000.")]
    fake_groq = _FakeGroqManager(raises=GroqAllKeysExhaustedError("all keys dead"))
    fake_visual = _FakeVisualProvider()

    image_paths = render_scene_images(scenes, fake_visual, project_id="test", groq_manager=fake_groq)

    assert len(image_paths) == 1
    # falls back to the static per-role template, which for BENEFIT is farmer-specific
    assert fake_visual.calls[0] == build_scene_image_prompt(scenes[0])
