"""Shared FastAPI dependencies."""
from __future__ import annotations

from app.config import Settings, get_settings

__all__ = ["get_settings", "Settings", "get_current_dev_user"]


def get_current_dev_user() -> str:
    """Placeholder identity for local dev — Phase 0 has no auth system.
    Real auth (even a simple one) is Phase 1+ scope."""
    return "dev-user"
