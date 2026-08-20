"""CloudflareVisualProvider — real Workers AI call + fallback-on-failure.
Requires CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN in the environment;
skipped otherwise (same convention as any test needing a real external
credential this repo can't assume CI has).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from core.models.enums import GenerationStatus, NarrativeRole, SceneType  # noqa: E402
from core.models.storyboard import Scene  # noqa: E402
from providers.visual.cloudflare_provider import CloudflareVisualProvider, _center_crop  # noqa: E402

_HAS_CREDS = bool(os.environ.get("CLOUDFLARE_ACCOUNT_ID")) and bool(os.environ.get("CLOUDFLARE_API_TOKEN"))


def _make_test_image_bytes(width: int, height: int) -> bytes:
    from io import BytesIO

    from PIL import Image

    img = Image.new("RGB", (width, height), (200, 100, 50))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_center_crop_produces_exact_requested_dimensions():
    """Regression guard for the real bug this replaced: FLUX.1-schnell's
    Workers AI endpoint rejects a width/height request parameter outright
    (verified live, 2026-08-20) — dimension control has to happen via a
    local crop after generation, not a request field."""
    from PIL import Image
    from io import BytesIO

    source = _make_test_image_bytes(1024, 1024)  # the model's real, fixed default output shape
    cropped = _center_crop(source, 1024, 768)
    img = Image.open(BytesIO(cropped))
    assert img.size == (1024, 768)


def test_center_crop_handles_a_source_narrower_than_the_target_ratio():
    """The 'source is relatively wider' branch and the 'source is
    relatively taller/narrower' branch are both real code paths — cover
    the one a square 1024x1024 source doesn't exercise (target wider
    than source)."""
    from PIL import Image
    from io import BytesIO

    source = _make_test_image_bytes(600, 1024)  # portrait source
    cropped = _center_crop(source, 1024, 768)  # landscape target
    img = Image.open(BytesIO(cropped))
    assert img.size == (1024, 768)


def _scene(text: str = "test") -> Scene:
    return Scene(
        storyboard_id="test", order_index=0, scene_type=SceneType.IMAGE_MOTION,
        narrative_role=NarrativeRole.CONTEXT, narration_segment_text=text, duration_seconds=3.0,
    )


@pytest.mark.skipif(not _HAS_CREDS, reason="CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN not set")
def test_generates_a_real_image(tmp_path):
    from providers.visual.local_cache import LocalCache

    provider = CloudflareVisualProvider(cache=LocalCache(tmp_path, extension="jpg"))
    asset = provider.generate_image(
        "a red bicycle on a paved path, photorealistic, no text, no signage",
        _scene(), project_id="test-project",
    )
    assert asset.generation_status == GenerationStatus.COMPLETE
    assert asset.provider_name.startswith("cloudflare:")
    assert asset.storage_path is not None
    path = Path(asset.storage_path)
    assert path.exists()
    assert path.stat().st_size > 10_000  # a real photo, not a tiny/corrupt file

    from PIL import Image
    with Image.open(path) as img:
        img.verify()


@pytest.mark.skipif(not _HAS_CREDS, reason="CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN not set")
def test_generates_a_real_image_at_a_requested_size(tmp_path):
    """End-to-end real-network cover for the width/height path — a
    different prompt than test_generates_a_real_image so this doesn't
    hit the LocalCache and skip the actual generation+crop call."""
    from providers.visual.local_cache import LocalCache

    provider = CloudflareVisualProvider(cache=LocalCache(tmp_path, extension="jpg"))
    asset = provider.generate_image(
        "a friendly professional news anchor sitting at a desk, photorealistic portrait, sized test",
        _scene(), project_id="test-project", width=1024, height=768,
    )
    assert asset.generation_status == GenerationStatus.COMPLETE
    assert asset.storage_path is not None
    from PIL import Image
    with Image.open(asset.storage_path) as img:
        assert img.size == (1024, 768)


def test_falls_back_to_placeholder_on_bad_credentials(tmp_path):
    from providers.visual.local_cache import LocalCache

    provider = CloudflareVisualProvider(
        account_id="bad-account", api_token="bad-token", cache=LocalCache(tmp_path, extension="jpg"),
    )
    asset = provider.generate_image("this should fail and fall back", _scene(), project_id="test-project")
    assert asset.generation_status == GenerationStatus.COMPLETE
    assert asset.provider_name == "local-placeholder"
    assert Path(asset.storage_path).exists()


def test_falls_back_to_placeholder_when_unconfigured(tmp_path, monkeypatch):
    from providers.visual.local_cache import LocalCache

    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    provider = CloudflareVisualProvider(cache=LocalCache(tmp_path, extension="jpg"))
    asset = provider.generate_image("no creds at all", _scene(), project_id="test-project")
    assert asset.provider_name == "local-placeholder"


def test_empty_prompt_raises():
    provider = CloudflareVisualProvider(account_id="x", api_token="y")
    with pytest.raises(ValueError):
        provider.generate_image("   ", _scene(), project_id="test-project")
