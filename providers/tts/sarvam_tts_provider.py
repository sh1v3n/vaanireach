"""SarvamTTSProvider — TTSProvider implementation with a hard vertical
failover to edge-tts.

Resilience shape (2-tier, matching AvatarFailoverManager's design in
Phase 3):
  Tier 1 (horizontal): SarvamTTSManager cycles through SARVAM_API_KEYS.
  Tier 2 (vertical):   if the whole Sarvam pool is exhausted/rate-limited,
                        the request is rejected, or a hard network failure
                        occurs, this class drops down to the edge-tts
                        Python library — free, local, no API key required
                        — so narration audio is always produced even with
                        zero working Sarvam keys.

Also owns the audio-prep utility (`process_and_slice_audio`) that splits
one generated track into the 5-second avatar "hook" clip and the
remaining "body" track for the B-roll voiceover, per docs/workflow.md's
TTS & Slicing pipeline stage.
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
from providers.tts.sarvam_client import (
    DEFAULT_SPEAKER as SARVAM_DEFAULT_SPEAKER,
    SarvamAllKeysExhaustedError,
    SarvamRequestError,
    SarvamTTSManager,
)
from providers.tts.sarvam_voices import VOICE_GENDER, model_for_voice

logger = logging.getLogger("vaanireach.providers.sarvam_tts_provider")

AUDIO_DIR = Path(os.environ.get("AUDIO_OUTPUT_DIR", "./data/audio"))
HOOK_DURATION_SECONDS = 5.0

# Sarvam's documented BCP-47 codes — all 9 of our LanguageCode members map
# directly (Sarvam also supports pa-IN/od-IN, which we don't model).
_LANG_TO_SARVAM: dict[LanguageCode, str] = {
    LanguageCode.EN: "en-IN",
    LanguageCode.HI: "hi-IN",
    LanguageCode.MR: "mr-IN",
    LanguageCode.BN: "bn-IN",
    LanguageCode.TA: "ta-IN",
    LanguageCode.TE: "te-IN",
    LanguageCode.KN: "kn-IN",
    LanguageCode.ML: "ml-IN",
    LanguageCode.GU: "gu-IN",
}

# edge-tts Neural voice names — verified against a live `edge-tts --list-voices`.
_LANG_TO_EDGE_VOICE: dict[LanguageCode, str] = {
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

# The curated, gender-tagged voice roster (providers/tts/sarvam_voices.py)
# — used for list_voices(). Sorted for a stable, predictable order.
SARVAM_SPEAKERS = sorted(VOICE_GENDER.keys())


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
    binary it installs automatically — no system ffmpeg required) decodes
    and re-encodes it to WAV, so every AudioAsset this provider produces
    — Sarvam- or edge-tts-sourced — ends up a WAV file on disk."""
    from moviepy import AudioFileClip  # local import: keep moviepy off the hot path for Sarvam-only runs

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


def slice_hook_and_body(audio_path: str, *, hook_seconds: float = HOOK_DURATION_SECONDS) -> tuple[str, str | None]:
    """Splits one WAV file into `<stem>_hook.wav` (first `hook_seconds`,
    for the Phase 3 avatar) and `<stem>_body.wav` (the remainder, for the
    B-roll voiceover). If the source track is shorter than `hook_seconds`,
    the whole thing becomes the hook and no body file is produced —
    logged, not raised, since a too-short script is a content issue, not
    a pipeline failure. Standalone module-level function (rather than
    provider-only) so it's usable on any cached WAV without needing a
    fully configured SarvamTTSProvider instance."""
    from moviepy import AudioFileClip

    source = Path(audio_path)
    stem = str(source.with_suffix(""))
    hook_path = Path(f"{stem}_hook.wav")
    body_path = Path(f"{stem}_body.wav")

    clip = AudioFileClip(str(source))
    try:
        duration = clip.duration
        hook_end = min(hook_seconds, duration)
        clip.subclipped(0, hook_end).write_audiofile(str(hook_path), codec="pcm_s16le", logger=None)

        if duration > hook_seconds:
            clip.subclipped(hook_seconds, duration).write_audiofile(str(body_path), codec="pcm_s16le", logger=None)
            return str(hook_path), str(body_path)

        logger.warning(
            "slice_hook_and_body: %s is only %.1fs (<= %.1fs hook target) — no body track produced",
            audio_path, duration, hook_seconds,
        )
        return str(hook_path), None
    finally:
        clip.close()


class SarvamTTSProvider(TTSProvider):
    def __init__(self, manager: SarvamTTSManager | None = None) -> None:
        if manager is not None:
            self.manager: SarvamTTSManager | None = manager
        else:
            try:
                self.manager = SarvamTTSManager()
            except ValueError:
                logger.warning(
                    "No SARVAM_API_KEYS configured — every synthesize() call this run will go "
                    "straight to edge-tts"
                )
                self.manager = None
        self._job_status: dict[str, GenerationStatus] = {}

    # ---------------------------------------------------------------- TTSProvider

    def list_voices(self, language: LanguageCode) -> list[str]:
        voices: list[str] = []
        if language in _LANG_TO_SARVAM:
            voices.extend(f"sarvam:{name}" for name in SARVAM_SPEAKERS)
        if language in _LANG_TO_EDGE_VOICE:
            voices.append(f"edge:{_LANG_TO_EDGE_VOICE[language]}")
        return voices

    def synthesize(
        self,
        text: str,
        language: LanguageCode,
        voice_id: str | None = None,
        *,
        project_id: str,
        script_id: str | None = None,
        pace: float = 1.0,
        pitch: float | None = None,
    ) -> AudioAsset:
        """NOTE: same documented Phase 0 interface gap as
        providers/llm/gemini_provider.py — AudioAsset.project_id is
        required by the model but the ABC signature doesn't pass it, so
        it's added here as a required keyword-only argument.

        pace/pitch are Sarvam-specific extras beyond the base
        TTSProvider ABC (like voice_id already was) — pitch is silently
        dropped by Sarvam itself unless the resolved speaker is a
        bulbul:v2 voice (see providers/tts/sarvam_voices.py's
        supports_pitch(); callers should check that before setting a
        non-default pitch, but this method never validates it — an
        unsupported pitch value is just ignored server-side, not an
        error here).

        Tries Sarvam first (horizontal key rotation inside
        SarvamTTSManager); on SarvamAllKeysExhaustedError, a rejected
        request, or any other hard failure, falls back to edge-tts — the
        vertical failover this whole provider exists for.
        """
        if not text.strip():
            raise ValueError("synthesize: text is empty")

        audio_bytes: bytes | None = None
        provider_name: str | None = None
        speaker = voice_id.removeprefix("sarvam:") if voice_id and voice_id.startswith("sarvam:") else SARVAM_DEFAULT_SPEAKER
        sarvam_lang = _LANG_TO_SARVAM.get(language)
        sarvam_model = model_for_voice(speaker)

        if self.manager is not None and sarvam_lang is not None:
            try:
                audio_bytes = self.manager.synthesize_text(
                    text, language_code=sarvam_lang, speaker=speaker, model=sarvam_model, pace=pace, pitch=pitch,
                )
                provider_name = f"sarvam:{sarvam_model}:{speaker}"
            except SarvamAllKeysExhaustedError as exc:
                logger.warning("synthesize: Sarvam pool exhausted (%s) — falling back to edge-tts", exc)
            except SarvamRequestError as exc:
                logger.warning("synthesize: Sarvam rejected the request (%s) — falling back to edge-tts", exc)
            except Exception as exc:  # noqa: BLE001 - any hard failure triggers the vertical failover too
                logger.warning("synthesize: unexpected Sarvam failure (%s) — falling back to edge-tts", exc)

        AUDIO_DIR.mkdir(parents=True, exist_ok=True)

        asset = AudioAsset(
            project_id=project_id,
            script_id=script_id,
            language=language,
            voice_id=voice_id,
            generation_status=GenerationStatus.IN_PROGRESS,
        )
        out_path = AUDIO_DIR / f"{asset.id}.wav"

        if audio_bytes is not None:
            out_path.write_bytes(audio_bytes)
        else:
            edge_voice = (
                voice_id.removeprefix("edge:") if voice_id and voice_id.startswith("edge:")
                else _LANG_TO_EDGE_VOICE.get(language)
            )
            if edge_voice is None:
                self._job_status[asset.id] = GenerationStatus.FAILED
                raise RuntimeError(
                    f"No TTS path available for language {language.value}: no Sarvam mapping "
                    "and no edge-tts voice configured"
                )
            try:
                mp3_bytes = _edge_tts_synthesize(text, edge_voice)
                _mp3_bytes_to_wav_file(mp3_bytes, out_path)
                provider_name = f"edge-tts:{edge_voice}"
            except Exception as exc:
                logger.error("synthesize: edge-tts fallback also failed (%s) — no audio produced", exc)
                self._job_status[asset.id] = GenerationStatus.FAILED
                raise

        asset.storage_path = str(out_path)
        asset.duration_seconds = _wav_duration_seconds(out_path)
        asset.tts_provider = provider_name
        asset.generation_status = GenerationStatus.COMPLETE
        self._job_status[asset.id] = GenerationStatus.COMPLETE
        return asset

    def get_status(self, job_id: str) -> GenerationStatus:
        """synthesize() is fully synchronous — it only returns once audio
        exists on disk or every fallback has failed — so there is no real
        async job to poll here. This just reports the last known outcome
        for a given AudioAsset id; an unrecognized id is reported FAILED
        rather than raising, since the ABC gives no "not found" signal."""
        status = self._job_status.get(job_id)
        if status is None:
            logger.warning("get_status: unknown job_id %s", job_id)
            return GenerationStatus.FAILED
        return status

    # ---------------------------------------------------------------- audio prep

    def process_and_slice_audio(
        self, audio_path: str, *, hook_seconds: float = HOOK_DURATION_SECONDS
    ) -> tuple[str, str | None]:
        """Splits a generated track into `hook_audio` (first
        `hook_seconds`, feeds the Phase 3 avatar) and `body_audio` (the
        remainder, plays under the B-roll). Thin wrapper over the
        module-level `slice_hook_and_body`."""
        return slice_hook_and_body(audio_path, hook_seconds=hook_seconds)
