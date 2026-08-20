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
from core.models.enums import GenerationStatus, NarrativeRole, SceneType  # noqa: E402
from core.models.narrative import VisualConcept  # noqa: E402
from core.models.storyboard import Scene  # noqa: E402
from providers.visual.local_cache import LocalCache  # noqa: E402
from rendering.adapters.cloudflare_scene_renderer import CloudflareSceneRenderer, build_scene_image_prompt  # noqa: E402

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
