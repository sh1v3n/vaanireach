"""HedraAvatarManager (v3 client): unit-level checks that don't need
network access — the API contract itself (v3 base URL, /files upload,
/models/{model} submit, /jobs/{id}/status + /jobs/{id} poll/download) was
verified live against the real Hedra API during a Task 9 end-to-end
investigation (2026-08-20): file uploads succeeded and a real generation
job was accepted through request validation, rejected only for
INSUFFICIENT_BALANCE (402) — see providers/video/hedra_client.py's
module docstring for the full contract this client implements.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.video.hedra_client import (  # noqa: E402
    HEDRA_BASE_URL, DEFAULT_MODEL, HedraAvatarManager, _HedraHTTPError, _classify_error,
)


def test_base_url_targets_v3_not_the_old_web_app_public_endpoint():
    """Regression guard for the actual bug this client was rewritten to
    fix: the old `https://api.hedra.com/web-app/public` base URL rejects
    every v3 API key with a 403 explaining the account has moved to v3."""
    assert HEDRA_BASE_URL == "https://api.hedra.com/v3"
    assert "web-app" not in HEDRA_BASE_URL


def test_default_model_is_the_longform_audio_to_video_model():
    assert DEFAULT_MODEL == "hedra-avatar"


def test_classify_error_treats_insufficient_balance_as_a_client_error():
    """Regression guard for the real 402 INSUFFICIENT_BALANCE response
    reproduced live: since the API wallet is shared across every key on
    an account, a balance failure on one key fails identically on every
    other key too — this must raise immediately (client_error), not burn
    through the whole key pool first."""
    exc = _HedraHTTPError(402, "Your API wallet balance is $0.00.")
    assert _classify_error(exc) == "client_error"


def test_classify_error_still_treats_auth_and_rate_limit_correctly():
    assert _classify_error(_HedraHTTPError(401, "unauthorized")) == "auth"
    assert _classify_error(_HedraHTTPError(403, "forbidden")) == "auth"
    assert _classify_error(_HedraHTTPError(429, "rate limited")) == "rate_limit"
    assert _classify_error(_HedraHTTPError(400, "bad request")) == "client_error"
    assert _classify_error(_HedraHTTPError(500, "server error")) == "transient"


def test_manager_construction_requires_no_stale_v2_model_id_kwargs():
    """Regression guard: the old client's __init__ accepted model_id=/
    model_slug= (v2-era, meaningless on v3, where the model is chosen by
    URL path, not a body field). The v3 client's constructor takes model=
    instead — passing the old kwarg names must fail loudly (TypeError),
    not be silently accepted and ignored."""
    import pytest
    with pytest.raises(TypeError):
        HedraAvatarManager(api_keys=["fake-key"], model_id="some-old-id")  # type: ignore[call-arg]
