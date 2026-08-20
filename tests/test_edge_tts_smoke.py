"""Step B smoke test (Phase 2 media plan): synthesize one short sentence
in English and one in Hindi via EdgeTtsProvider, confirm playable audio +
duration. Run directly: python tests/test_edge_tts_smoke.py
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models.enums import LanguageCode, GenerationStatus  # noqa: E402
from providers.tts.edge_tts_provider import EdgeTtsProvider  # noqa: E402

SAMPLES = [
    (LanguageCode.EN, "This scheme provides a subsidy of five thousand rupees."),
    (LanguageCode.HI, "यह योजना पाँच हज़ार रुपये की सब्सिडी प्रदान करती है।"),
]


def main() -> None:
    provider = EdgeTtsProvider()
    results = []

    for language, text in SAMPLES:
        print(f"\n=== Synthesizing [{language.value}]: {text!r}")
        asset = provider.synthesize(text, language, project_id="smoke-test-step-b")

        assert asset.generation_status == GenerationStatus.COMPLETE, "synthesis did not complete"
        assert asset.storage_path is not None, "no storage_path on returned AudioAsset"
        assert Path(asset.storage_path).exists(), f"audio file missing: {asset.storage_path}"
        assert asset.duration_seconds is not None and asset.duration_seconds > 0, "no valid duration"

        # Confirm the WAV file is genuinely playable audio: openable via the
        # `wave` module, real frames, real sample rate/channels.
        with wave.open(asset.storage_path, "rb") as wav_file:
            n_frames = wav_file.getnframes()
            frame_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()

        assert n_frames > 0, "WAV file has zero frames"
        assert frame_rate > 0, "WAV file has invalid frame rate"

        file_size = Path(asset.storage_path).stat().st_size
        print(f"  voice:            {asset.voice_id}")
        print(f"  tts_provider:     {asset.tts_provider}")
        print(f"  storage_path:     {asset.storage_path}")
        print(f"  file size:        {file_size} bytes")
        print(f"  duration_seconds: {asset.duration_seconds:.3f}s")
        print(f"  WAV frames:       {n_frames} @ {frame_rate}Hz, {channels}ch, {sample_width*8}-bit")
        print(f"  generation_status:{asset.generation_status.value}")

        results.append((language, asset))

    print("\n=== Step B result: PASS — both languages produced playable audio with a real duration ===")
    for language, asset in results:
        print(f"  [{language.value}] {asset.duration_seconds:.3f}s -> {asset.storage_path}")


if __name__ == "__main__":
    main()
