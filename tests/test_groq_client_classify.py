"""Unit coverage for GroqManager._classify_error — pure function, no
network calls. Exercises the exact error strings captured live from this
project's own Groq account on 2026-08-20 (see groq_client.py's module
docstring), not synthetic approximations, since the empty-vs-populated
`failed_generation` distinction is the whole point of the fix being
tested here.
"""
from __future__ import annotations

from providers.llm.groq_client import _classify_error

# Captured verbatim (minus a truncation point) from a real 400 hit while
# fact-extraction/script-generation calls were contending for a single
# key's TPM budget — the model produced nothing at all to validate.
_EMPTY_FAILED_GENERATION_BODY = (
    '{"error":{"message":"Failed to validate JSON. Please adjust your prompt. '
    "See 'failed_generation' for more details.\",\"type\":\"invalid_request_error\","
    '"code":"json_validate_failed","failed_generation":""}}'
)

# Captured verbatim from a real 400 during B-roll prompt drafting — the
# model DID produce output, it just wasn't valid JSON.
_POPULATED_FAILED_GENERATION_BODY = (
    '{"error":{"message":"Failed to generate JSON. Please adjust your prompt. '
    "See 'failed_generation' for more details.\",\"type\":\"invalid_request_error\","
    '"code":"json_validate_failed","failed_generation":"[\\"Portrait orientation, '
    'early morning golden light over a modest Indian farm field, a small farmer in"}}'
)

_REAL_429_BODY = (
    '{"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b` in organization '
    '`org_01kd83f5vffez8rtre5g9cm791` service tier `on_demand` on tokens per minute (TPM): '
    'Limit 8000, Used 7729, Requested 2488. Please try again in 16.6275s."}}'
)


def test_empty_failed_generation_400_classifies_as_rate_limit() -> None:
    """The TPM-exhaustion signature this fix targets: a 400
    json_validate_failed with nothing in failed_generation must be
    treated as retryable, not a hard failure — otherwise a single-key
    pool exhausts on the very next call after a real 429 already waited
    out the window once."""
    assert _classify_error(400, _EMPTY_FAILED_GENERATION_BODY) == "rate_limit"


def test_populated_failed_generation_400_stays_unknown() -> None:
    """A 400 where Groq DID produce (invalid) output is a genuinely bad
    prompt/schema mismatch, not a budget artifact — must NOT be
    reclassified as rate_limit, or a real prompt bug would silently retry
    forever instead of surfacing."""
    assert _classify_error(400, _POPULATED_FAILED_GENERATION_BODY) == "unknown"


def test_real_429_still_classifies_as_rate_limit() -> None:
    """Regression check: the new empty-failed_generation branch must not
    shadow the existing, already-correct 429 handling."""
    assert _classify_error(429, _REAL_429_BODY) == "rate_limit"


def test_unrelated_400_stays_unknown() -> None:
    """A plain, unrelated 400 (no json_validate_failed marker at all)
    must not be swept into rate_limit just because it's a 400."""
    body = '{"error":{"message":"Invalid value for temperature","type":"invalid_request_error"}}'
    assert _classify_error(400, body) == "unknown"
