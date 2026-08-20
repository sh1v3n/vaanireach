"""EdgeTtsProvider — the guaranteed TTSProvider implementation (Phase 2
media plan). Free, local-network-only, no API key, no billing dependency
to fail on. This is the critical-path TTS: SarvamTTSProvider already
carries edge-tts as ITS vertical fallback, but the guaranteed pipeline
depends on edge-tts directly, not on Sarvam being configured at all.

No fallback tier is implemented here on purpose — per the Phase 2 plan,
only a network outage could break edge-tts, which is an acceptable
hard-stop for a locally-run TTS call.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import wave
from pathlib import Path

import edge_tts

from core.interfaces.tts_provider import TTSProvider
from core.models.enums import GenerationStatus, LanguageCode
from core.models.media import AudioAsset

logger = logging.getLogger("vaanireach.providers.edge_tts_provider")

AUDIO_DIR = Path(os.environ.get("AUDIO_OUTPUT_DIR", "./data/audio"))

# edge-tts Neural voice names — verified against a live `edge-tts --list-voices`
# (same mapping SarvamTTSProvider uses for its own edge-tts fallback tier).
_LANG_TO_VOICE: dict[LanguageCode, str] = {
    LanguageCode.EN: "en-IN-NeerjaNeural",
    LanguageCode.HI: "hi-IN-MadhurNeural",
    LanguageCode.MR: "mr-IN-AarohiNeural",
    LanguageCode.BN: "bn-IN-BashkarNeural",
    LanguageCode.TA: "ta-IN-PallaviNeural",
    LanguageCode.TE: "te-IN-ShrutiNeural",
    LanguageCode.KN: "kn-IN-SapnaNeural",
    LanguageCode.ML: "ml-IN-SobhanaNeural",
    LanguageCode.GU: "gu-IN-DhwaniNeural",
}


def _edge_tts_synthesize(text: str, voice: str) -> bytes:
    """Runs edge-tts's async streaming API synchronously and returns raw
    MP3 bytes (edge-tts has no WAV output mode)."""

    async def _run() -> bytes:
        communicate = edge_tts.Communicate(text, voice)
        buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.write(chunk["data"])
        return buffer.getvalue()

    return asyncio.run(_run())


def _mp3_bytes_to_wav_file(mp3_bytes: bytes, out_path: Path) -> None:
    """edge-tts only emits MP3; moviepy (backed by the imageio-ffmpeg
    binary it installs automatically, or system ffmpeg when present)
    decodes and re-encodes it to WAV so every AudioAsset this provider
    produces ends up a WAV file on disk — same convention as
    SarvamTTSProvider."""
    from moviepy import AudioFileClip  # local import: keep moviepy off the hot path when unused

    tmp_mp3 = out_path.with_suffix(".tmp.mp3")
    tmp_mp3.write_bytes(mp3_bytes)
    try:
        clip = AudioFileClip(str(tmp_mp3))
        try:
            clip.write_audiofile(str(out_path), codec="pcm_s16le", logger=None)
        finally:
            clip.close()
    finally:
        tmp_mp3.unlink(missing_ok=True)


def _wav_duration_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wav_file:
            return wav_file.getnframes() / float(wav_file.getframerate())
    except (wave.Error, OSError) as exc:
        logger.warning("Could not read WAV duration for %s: %s", path, exc)
        return None


class EdgeTtsProvider(TTSProvider):
    def __init__(self) -> None:
        self._job_status: dict[str, GenerationStatus] = {}

    # ---------------------------------------------------------------- TTSProvider

    def list_voices(self, language: LanguageCode) -> list[str]:
        voice = _LANG_TO_VOICE.get(language)
        return [voice] if voice else []

    def synthesize(
        self,
        text: str,
        language: LanguageCode,
        voice_id: str | None = None,
        *,
        project_id: str,
        script_id: str | None = None,
    ) -> AudioAsset:
        """NOTE: same documented Phase 0 interface gap as
        providers/tts/sarvam_tts_provider.py — AudioAsset.project_id is
        required by the model but the ABC signature doesn't pass it, so
        it's added here as a required keyword-only argument.

        Audio duration is the source of truth for scene timing per the
        Phase 2 plan: callers read AudioAsset.duration_seconds off the
        returned asset BEFORE any visual rendering happens for that scene.
        """
        if not text.strip():
            raise ValueError("synthesize: text is empty")

        voice = voice_id or _LANG_TO_VOICE.get(language)
        if voice is None:
            raise RuntimeError(f"No edge-tts voice configured for language {language.value}")

        AUDIO_DIR.mkdir(parents=True, exist_ok=True)

        asset = AudioAsset(
            project_id=project_id,
            script_id=script_id,
            language=language,
            voice_id=voice,
            generation_status=GenerationStatus.IN_PROGRESS,
        )
        out_path = AUDIO_DIR / f"{asset.id}.wav"

        try:
            mp3_bytes = _edge_tts_synthesize(text, voice)
            _mp3_bytes_to_wav_file(mp3_bytes, out_path)
        except Exception as exc:
            logger.error("synthesize: edge-tts failed (%s) — no audio produced", exc)
            self._job_status[asset.id] = GenerationStatus.FAILED
            raise

        asset.storage_path = str(out_path)
        asset.duration_seconds = _wav_duration_seconds(out_path)
        asset.tts_provider = f"edge-tts:{voice}"
        asset.generation_status = GenerationStatus.COMPLETE
        self._job_status[asset.id] = GenerationStatus.COMPLETE
        return asset

    def get_status(self, job_id: str) -> GenerationStatus:
        """synthesize() is fully synchronous — it only returns once audio
        exists on disk or the call has failed — so there is no real async
        job to poll here. This just reports the last known outcome for a
        given AudioAsset id; an unrecognized id is reported FAILED rather
        than raising, since the ABC gives no "not found" signal."""
        status = self._job_status.get(job_id)
        if status is None:
            logger.warning("get_status: unknown job_id %s", job_id)
            return GenerationStatus.FAILED
        return status
