"""D-ID audio-upload compression: /audios enforces a payload-size cap a
raw 48kHz/16-bit/stereo PCM WAV can exceed for anything beyond a very
short clip — confirmed by direct reproduction against the real API
(2026-08-20): a 35s/6.7MB WAV got 413 Request Too Long; the same audio
re-encoded to 128kbps MP3 (~560KB) uploaded successfully. See
providers/video/did_client.py's module docstring.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from providers.video.did_client import _compress_audio_for_upload, _classify_error, _DIDHTTPError  # noqa: E402


def _make_wav(path: Path, *, duration: float) -> None:
    # 48kHz stereo PCM, matching rendering/adapters/ffmpeg_video_renderer.py's
    # concat_audio_files output format — the exact shape that triggered the
    # real 413 during reproduction.
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-ar", "48000", "-ac", "2", str(path)],
        check=True, timeout=30,
    )


def test_compress_audio_for_upload_shrinks_a_long_wav_well_below_the_413_threshold(tmp_path):
    """The real failure was a 35s/6.7MB WAV; the real fix was a ~560KB
    MP3 of the same audio. Reproduces the shape (not the exact bytes) and
    asserts the same order-of-magnitude size reduction."""
    wav_path = tmp_path / "long.wav"
    _make_wav(wav_path, duration=35.0)
    wav_size = wav_path.stat().st_size
    assert wav_size > 5_000_000, f"test WAV fixture is {wav_size} bytes — expected several MB to reproduce the real failure shape"

    mp3_path = Path(_compress_audio_for_upload(str(wav_path)))
    try:
        assert mp3_path.exists()
        mp3_size = mp3_path.stat().st_size
        assert mp3_size < 1_000_000, f"compressed MP3 is {mp3_size} bytes — expected well under 1MB (real fix produced ~560KB for 35s)"
        assert mp3_size < wav_size / 5  # a real, substantial size reduction, not a no-op
    finally:
        mp3_path.unlink(missing_ok=True)


def test_compress_audio_for_upload_preserves_duration(tmp_path):
    wav_path = tmp_path / "sample.wav"
    _make_wav(wav_path, duration=10.0)

    mp3_path = Path(_compress_audio_for_upload(str(wav_path)))
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp3_path)],
            capture_output=True, text=True, timeout=15,
        )
        actual_duration = float(probe.stdout.strip())
        assert actual_duration == pytest.approx(10.0, abs=0.5)  # mp3 encoder framing can round slightly
    finally:
        mp3_path.unlink(missing_ok=True)


def test_compress_audio_for_upload_raises_clearly_on_a_nonexistent_input(tmp_path):
    with pytest.raises(RuntimeError, match="ffmpeg transcode failed"):
        _compress_audio_for_upload(str(tmp_path / "does_not_exist.wav"))


def test_classify_error_treats_not_enough_credits_as_a_client_error():
    """Regression guard for the real 402 'not enough credits' response
    seen on a real run (final whole-branch review, finding #3): D-ID's
    credit balance/plan limit is an account-level property, so a credits
    failure on one key fails identically on every other key on the same
    account — this must raise immediately (client_error), not burn
    through the whole key pool re-uploading first. Mirrors
    test_hedra_v3_client.py::test_classify_error_treats_insufficient_balance_as_a_client_error."""
    exc = _DIDHTTPError(402, "not enough credits")
    assert _classify_error(exc) == "client_error"


def test_classify_error_still_treats_auth_and_rate_limit_correctly():
    assert _classify_error(_DIDHTTPError(401, "unauthorized")) == "auth"
    assert _classify_error(_DIDHTTPError(403, "forbidden")) == "auth"
    assert _classify_error(_DIDHTTPError(429, "rate limited")) == "rate_limit"
    assert _classify_error(_DIDHTTPError(400, "bad request")) == "client_error"
    assert _classify_error(_DIDHTTPError(500, "server error")) == "transient"
