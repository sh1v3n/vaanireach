"""Filename sanitization + storage path generation for uploaded documents.

Real implementation (not a stub) — cheap, provider-independent, and
directly needed to avoid path traversal / collision issues whenever
uploads are wired up in Phase 1.
"""
from __future__ import annotations

import re
import uuid
from pathlib import PurePosixPath

_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(original: str) -> str:
    """Strips any path components and restricts the name to a safe
    charset, then appends a short uuid suffix (before the extension) to
    avoid collisions between uploads with the same display name."""
    base_name = PurePosixPath(original.replace("\\", "/")).name or "upload"

    if "." in base_name:
        stem, _, extension = base_name.rpartition(".")
        extension = "." + _SAFE_CHARS_RE.sub("", extension)[:10]
    else:
        stem, extension = base_name, ""

    stem = _SAFE_CHARS_RE.sub("_", stem).strip("._") or "upload"
    suffix = uuid.uuid4().hex[:8]
    return f"{stem}_{suffix}{extension}"


def generate_storage_path(project_id: str, document_id: str, sanitized_name: str) -> str:
    """Builds a namespaced relative storage path. Callers are responsible
    for joining this onto the configured UPLOAD_DIR and creating parent
    directories."""
    return f"{project_id}/{document_id}/{sanitized_name}"
