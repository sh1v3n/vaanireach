"""Cross-cutting security constants shared by the backend and (later)
agents/providers. Concrete file-validation / filename-sanitization logic
lives in backend/app/security/ since it's currently only needed at the
upload boundary; if agents start handling files directly in a later phase,
that logic should move here so it isn't duplicated.

Everything below is documentation-as-defaults, not enforcement — the
backend is what actually applies these.
"""
from __future__ import annotations

DEFAULT_MAX_UPLOAD_SIZE_MB = 25
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 30

# TODO (Phase 1+): rate limiting on public-facing routes (e.g. slowapi or a
# reverse-proxy layer) — not implemented in Phase 0.
# TODO (Phase 1+): structured request/response logging with secrets
# redacted — not implemented in Phase 0.
