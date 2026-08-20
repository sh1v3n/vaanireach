"""avatar_portrait: the fixed, shared presenter portrait the template
pipeline's avatar PiP overlay animates - generated once, served from
CloudflareVisualProvider's own LocalCache on every later call."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models.enums import GenerationStatus, MediaAssetType  # noqa: E402
from core.models.media import MediaAsset  # noqa: E402
from providers.video.avatar_portrait import AVATAR_IMAGE_PROMPT, SHARED_ASSET_PROJECT_ID, get_avatar_source_image  # noqa: E402


class _FakeVisualProvider:
    """Records every generate_image call instead of hitting the network -
    fast, deterministic test of avatar_portrait's own logic (prompt/
    project_id/scene shape passed through), not CloudflareVisualProvider's."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []  # (prompt, project_id)

    def generate_image(self, prompt, scene, *, project_id):
        self.calls.append((prompt, project_id))
        return MediaAsset(
            project_id=project_id, scene_id=scene.id, asset_type=MediaAssetType.IMAGE,
            storage_path="/tmp/fake-avatar-portrait.jpg", provider_name="fake",
            generation_status=GenerationStatus.COMPLETE,
        )


def test_get_avatar_source_image_uses_the_fixed_prompt_and_shared_project_id():
    fake = _FakeVisualProvider()
    path = get_avatar_source_image(fake)
    assert path == "/tmp/fake-avatar-portrait.jpg"
    assert len(fake.calls) == 1
    prompt, project_id = fake.calls[0]
    assert prompt == AVATAR_IMAGE_PROMPT
    assert project_id == SHARED_ASSET_PROJECT_ID


def test_calling_twice_still_passes_the_same_prompt_both_times():
    """Regression guard: the prompt must be byte-identical across calls,
    since CloudflareVisualProvider's LocalCache is keyed on the exact
    prompt string - a prompt that drifts (e.g. an interpolated field)
    would silently defeat the cache and hit the network every time."""
    fake = _FakeVisualProvider()
    get_avatar_source_image(fake)
    get_avatar_source_image(fake)
    assert fake.calls[0][0] == fake.calls[1][0]
