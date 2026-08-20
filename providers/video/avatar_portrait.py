"""avatar_portrait — the fixed, shared presenter portrait the template
pipeline's avatar PiP overlay (rendering/multilingual_video.py) animates
via AvatarFailoverProvider.generate_avatar_hook(). Generated once (keyed
by this exact prompt string in CloudflareVisualProvider's own
LocalCache) and reused across every project/video — "one consistent
on-screen presenter", not scheme-specific content.

Parallel to dashboard/app.py's get_avatar_source_image/AVATAR_IMAGE_PROMPT,
but targeting CloudflareVisualProvider instead of HuggingFaceVisualProvider
— the dashboard pipeline and the template pipeline (this module's caller)
use different VisualProvider implementations, so this is a small, deliberate
duplication rather than a shared import across the two pipelines (see
docs/superpowers/specs/2026-08-20-video-captions-avatar-shortening-design.md,
which keeps the two pipelines independent by design).
"""
from __future__ import annotations

from core.models.enums import NarrativeRole, SceneType
from core.models.storyboard import Scene
from providers.visual.cloudflare_provider import CloudflareVisualProvider

SHARED_ASSET_PROJECT_ID = "vaanireach-shared-assets"
AVATAR_IMAGE_PROMPT = (
    "A friendly, professional Indian government outreach spokesperson: warm, approachable "
    "expression, looking directly at the camera, plain neutral studio background, upper-body "
    "portrait, soft even lighting, photorealistic, no text or logos in frame"
)


def get_avatar_source_image(visual_provider: CloudflareVisualProvider) -> str:
    """Returns a local file path to the presenter portrait — served from
    LocalCache on every call after the first (the cache is keyed on the
    exact AVATAR_IMAGE_PROMPT string), so this only ever hits the
    network once across the process's lifetime."""
    placeholder_scene = Scene(
        storyboard_id="shared-avatar-source", order_index=0, scene_type=SceneType.IMAGE_MOTION,
        narrative_role=NarrativeRole.HOOK,
        narration_segment_text="avatar source portrait", duration_seconds=1.0,
    )
    asset = visual_provider.generate_image(AVATAR_IMAGE_PROMPT, placeholder_scene, project_id=SHARED_ASSET_PROJECT_ID)
    assert asset.storage_path is not None  # generate_image always sets this on success, including its own placeholder-card fallback
    return asset.storage_path
