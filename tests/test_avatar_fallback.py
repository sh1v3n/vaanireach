"""Tier 3 avatar fallback: build_static_fallback_clip() and
AvatarFailoverProvider.generate_avatar_hook()'s use of it when both real
tiers are exhausted. Added 2026-08-20 alongside the fix itself — the
original Tier 3 (ensure_fallback_asset's shared, content-independent,
SILENT generic placeholder) meant the hook's real narration was audible
nowhere in the final video whenever Hedra/D-ID both failed, confirmed
live. These tests assert the replacement actually carries the real audio
and a duration derived from it, not the old fixed 5s.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.models.enums import GenerationStatus
from providers.video.avatar_provider import (
    FALLBACK_DURATION_SECONDS,
    AvatarFailoverProvider,
    build_static_fallback_clip,
    ensure_fallback_asset,
)
from providers.video.did_client import DIDAllKeysExhaustedError
from providers.video.hedra_client import HedraAllKeysExhaustedError

PROJECT_ID = "proj-avatar-fallback-test"


class _AlwaysFailHedra:
    """Stands in for a real HedraAvatarManager whose key pool is
    exhausted — no network, no env vars, deterministic."""

    def generate_avatar_video(self, *args, **kwargs):
        raise HedraAllKeysExhaustedError("no Hedra keys configured (test double)")


class _AlwaysFailDID:
    def generate_avatar_video(self, *args, **kwargs):
        raise DIDAllKeysExhaustedError("no D-ID keys configured (test double)")


def _make_presenter_image(tmp_path: Path) -> str:
    from PIL import Image

    path = tmp_path / "presenter.jpg"
    Image.new("RGB", (600, 800), color=(200, 180, 160)).save(path, format="JPEG")
    return str(path)


def _make_narration_audio(tmp_path: Path, *, duration_seconds: float = 4.2) -> str:
    import numpy as np
    from moviepy import AudioClip

    path = tmp_path / "narration.wav"
    clip = AudioClip(lambda t: 0.1 * np.sin(2 * np.pi * 440 * t), duration=duration_seconds, fps=24000)
    try:
        clip.write_audiofile(str(path), codec="pcm_s16le", logger=None)
    finally:
        clip.close()
    return str(path)


def test_build_static_fallback_clip_bakes_in_the_real_audio(tmp_path) -> None:
    image_path = _make_presenter_image(tmp_path)
    audio_path = _make_narration_audio(tmp_path, duration_seconds=4.2)
    out_path = tmp_path / "fallback_out.mp4"

    result_path = build_static_fallback_clip(image_path, audio_path, out_path)

    assert Path(result_path).exists()
    assert Path(result_path).stat().st_size > 0

    from moviepy import VideoFileClip

    clip = VideoFileClip(result_path)
    try:
        assert clip.audio is not None
        # Duration follows the REAL audio, not the old fixed
        # FALLBACK_DURATION_SECONDS (5.0s) — the whole point of the fix.
        assert clip.duration == pytest.approx(4.2, abs=0.3)
        assert clip.size == [720, 1280] or tuple(clip.size) == (720, 1280)
    finally:
        clip.close()


def test_generate_avatar_hook_falls_back_to_audio_matched_clip_when_both_tiers_fail(tmp_path) -> None:
    image_path = _make_presenter_image(tmp_path)
    audio_path = _make_narration_audio(tmp_path, duration_seconds=6.0)

    provider = AvatarFailoverProvider(hedra=_AlwaysFailHedra(), did=_AlwaysFailDID())
    asset = provider.generate_avatar_hook(image_path, audio_path, project_id=PROJECT_ID)

    assert asset.generation_status == GenerationStatus.COMPLETE
    assert asset.provider_name == "local-fallback"
    assert asset.metadata == {"tier": 3}
    assert asset.storage_path is not None
    assert Path(asset.storage_path).exists()
    # Must NOT be the old shared generic placeholder — that one is fixed
    # at FALLBACK_DURATION_SECONDS and carries no real audio.
    assert Path(asset.storage_path) != Path(ensure_fallback_asset())

    from moviepy import VideoFileClip

    clip = VideoFileClip(asset.storage_path)
    try:
        assert clip.audio is not None
        assert clip.duration == pytest.approx(6.0, abs=0.3)
        assert clip.duration != pytest.approx(FALLBACK_DURATION_SECONDS, abs=0.05)
    finally:
        clip.close()
