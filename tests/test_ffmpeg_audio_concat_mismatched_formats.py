"""Regression test: FfmpegVideoRenderer._concat_audio must produce
correct output even when input WAV files have DIFFERENT sample
rates/channel counts — exactly the situation once SarvamTTSProvider
(24kHz mono on success) and its own edge-tts fallback (44.1kHz stereo)
can both appear across scenes in the same video.

Found by direct reproduction: ffmpeg's concat DEMUXER with `-c copy`
(stream-copy, no re-encode) silently produces a WRONG total duration
when inputs don't share the same format — no error, exit code 0, just
corrupted audio. This test locks in the fix (re-encoding via a filter
graph) so it can't regress silently again.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from rendering.adapters.ffmpeg_video_renderer import concat_audio_files  # noqa: E402


def _make_tone(path: Path, *, duration: float, sample_rate: int, channels: int) -> None:
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-ar", str(sample_rate), "-ac", str(channels),
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def test_concat_of_mismatched_sample_rates_and_channels_is_correct(tmp_path):
    """The exact bug scenario: one 24kHz mono clip (Sarvam's real output
    format) + one 44.1kHz stereo clip (edge-tts's output format), same
    as SarvamTTSProvider's own vertical fallback would produce mid-run."""
    clip_a = tmp_path / "a_24k_mono.wav"
    clip_b = tmp_path / "b_44k_stereo.wav"
    _make_tone(clip_a, duration=2.0, sample_rate=24000, channels=1)
    _make_tone(clip_b, duration=2.0, sample_rate=44100, channels=2)

    out_path = concat_audio_files([str(clip_a), str(clip_b)], tmp_path)

    actual_duration = _probe_duration(out_path)
    assert actual_duration == pytest.approx(4.0, abs=0.1), (
        f"expected ~4.0s (2.0+2.0), got {actual_duration:.3f}s — mismatched-format concat is broken again"
    )


def test_concat_of_matching_formats_still_works(tmp_path):
    """Sanity check the fix didn't break the common case (all clips same format)."""
    clip_a = tmp_path / "a.wav"
    clip_b = tmp_path / "b.wav"
    clip_c = tmp_path / "c.wav"
    for clip, dur in [(clip_a, 1.5), (clip_b, 2.5), (clip_c, 1.0)]:
        _make_tone(clip, duration=dur, sample_rate=24000, channels=1)

    out_path = concat_audio_files([str(clip_a), str(clip_b), str(clip_c)], tmp_path)
    actual_duration = _probe_duration(out_path)
    assert actual_duration == pytest.approx(5.0, abs=0.1)


def test_concat_audio_files_is_importable_at_module_level(tmp_path):
    """Regression guard for the Task 4 extraction: concat_audio_files
    must be usable without a FfmpegVideoRenderer instance, since
    rendering/multilingual_video.py calls it directly to build the full
    narration audio track for avatar lip-sync."""
    from rendering.adapters.ffmpeg_video_renderer import concat_audio_files
    import subprocess

    # two short real WAV files via ffmpeg's own sine generator - avoids a binary fixture file
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(a)], check=True, timeout=15)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=1.5", str(b)], check=True, timeout=15)

    out_path = concat_audio_files([str(a), str(b)], tmp_path)
    assert out_path.exists()

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out_path)],
        capture_output=True, text=True, timeout=15,
    )
    assert float(probe.stdout.strip()) == pytest.approx(2.5, abs=0.05)
