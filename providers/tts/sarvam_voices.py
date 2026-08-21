"""sarvam_voices — the curated, gender-tagged Sarvam speaker roster this
pipeline exposes to users. Single source of truth for both the TTS layer
(providers/tts/sarvam_tts_provider.py, which needs the right model per
speaker) and the backend job API (which derives avatar gender from the
user's chosen voice) — one place to update if Sarvam adds/renames voices,
not two copies that can drift.

Deliberately NOT the full Sarvam catalogue (36 bulbul:v3 + 7 bulbul:v2 =
43 named speakers as of this writing) — only the subset Sarvam's own docs
(docs.sarvam.ai/api-reference/text-to-speech) explicitly gender-tag is
exposed here. Getting a voice/avatar gender mismatch wrong is worse than
offering fewer voices; the ~17 newer bulbul:v3 speakers with no
documented gender tag (kabir, aayan, ashutosh, ...) are excluded rather
than guessed at from their names.

Pitch/loudness are Sarvam parameters that ONLY work on bulbul:v2 (the
older, smaller 7-voice set) — NOT on bulbul:v3 (this pipeline's default,
36-voice set). Rather than force everyone onto the smaller v2 roster to
get pitch control, v3's full voice variety stays the default and pitch
is only offered for the 7 v2 voices — see supports_pitch() below.
"""
from __future__ import annotations

from typing import Literal

VoiceGender = Literal["male", "female"]

BULBUL_V3 = "bulbul:v3"
BULBUL_V2 = "bulbul:v2"

# bulbul:v3 — the default, richer voice set (no pitch/loudness support).
_V3_MALE = ["shubh", "aditya", "rahul", "rohan", "amit", "dev", "ratan", "varun", "manan", "sumit"]
_V3_FEMALE = ["ritu", "priya", "neha", "pooja", "simran", "kavya", "ishita", "shreya", "roopa", "tanya"]

# bulbul:v2 — smaller, older voice set; the ONLY voices with real pitch/loudness control.
_V2_MALE = ["abhilash", "karun", "hitesh"]
_V2_FEMALE = ["anushka", "manisha", "vidya", "arya"]

VOICE_GENDER: dict[str, VoiceGender] = {
    **{name: "male" for name in _V3_MALE},
    **{name: "female" for name in _V3_FEMALE},
    **{name: "male" for name in _V2_MALE},
    **{name: "female" for name in _V2_FEMALE},
}

VOICE_MODEL: dict[str, str] = {
    **{name: BULBUL_V3 for name in (*_V3_MALE, *_V3_FEMALE)},
    **{name: BULBUL_V2 for name in (*_V2_MALE, *_V2_FEMALE)},
}

DEFAULT_SPEAKER = "shubh"  # matches Sarvam's own bulbul:v3 default


def voices_by_gender(gender: VoiceGender) -> list[str]:
    return sorted(name for name, g in VOICE_GENDER.items() if g == gender)


def gender_for_voice(speaker: str) -> VoiceGender:
    """Defaults to "male" for an unrecognized speaker name (e.g. a raw
    Sarvam voice not in this curated list, passed through directly) —
    a safe, harmless fallback since it only affects which avatar
    portrait is used, never narration/audio correctness."""
    return VOICE_GENDER.get(speaker.lower(), "male")


def model_for_voice(speaker: str) -> str:
    return VOICE_MODEL.get(speaker.lower(), BULBUL_V3)


def supports_pitch(speaker: str) -> bool:
    return model_for_voice(speaker) == BULBUL_V2
