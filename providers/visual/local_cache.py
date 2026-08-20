"""LocalCache — content-addressed on-disk cache for expensive,
prompt-in/bytes-out generation calls.

Image generation is the single biggest API bottleneck in the pipeline:
slower and more rate-limit-sensitive than a text call, and during
iteration/demo runs the same B-roll prompt (or one with only trivial
whitespace differences) gets requested repeatedly. This cache turns a
repeat request into one `stat()` call instead of a network round trip.

Deliberately generic — keyed on the prompt text, not on Gemini/Imagen
specifics — so any future VisualProvider (or AudioProvider, if
background-music prompts ever need the same treatment) can reuse it
without coupling to `providers/visual/gemini_imagen_provider.py`.
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

logger = logging.getLogger("vaanireach.providers.local_cache")

_SLUG_UNSAFE_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_len: int = 40) -> str:
    """Lowercases, collapses everything but [a-z0-9] into single hyphens,
    and truncates. Used only for a human-skimmable filename prefix — the
    hash suffix (see `cache_key`) is what actually guarantees uniqueness,
    so truncation/collisions here are harmless."""
    slug = _SLUG_UNSAFE_RE.sub("-", text.strip().lower()).strip("-")
    return slug[:max_len] or "prompt"


def cache_key(prompt: str) -> str:
    """`{slug}-{sha256[:16]}` — the slug keeps cache directory listings
    readable during debugging, the hash prefix is the actual identity so
    two prompts that slugify the same (e.g. differing only in punctuation
    or casing) never collide."""
    digest = hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest()[:16]
    return f"{slugify(prompt)}-{digest}"


class LocalCache:
    """A flat directory of `{cache_key(prompt)}.{extension}` files.

    Not concurrency-safe across processes beyond what `put`'s
    write-then-rename gives (a reader never observes a partially written
    file) — sufficient for this single-process pipeline; not intended as
    a general-purpose distributed cache.
    """

    def __init__(self, root: str | Path, *, extension: str = "jpg") -> None:
        self.root = Path(root)
        self.extension = extension.lstrip(".")
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, prompt: str) -> Path:
        return self.root / f"{cache_key(prompt)}.{self.extension}"

    def get(self, prompt: str) -> str | None:
        """Returns the cached file's path if present and non-empty,
        else None. Never raises — a corrupt/zero-byte cache entry is
        treated as a miss so the caller regenerates it."""
        path = self.path_for(prompt)
        if path.exists() and path.stat().st_size > 0:
            logger.info("LocalCache HIT for prompt=%r -> %s", prompt[:60], path.name)
            return str(path)
        return None

    def put(self, prompt: str, data: bytes) -> str:
        """Writes `data` to a temp file and renames it into place —
        `Path.replace` is atomic on both POSIX and Windows, so a reader
        never sees a half-written cache entry even if two calls race on
        the same prompt."""
        path = self.path_for(prompt)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_bytes(data)
        tmp_path.replace(path)
        logger.info("LocalCache MISS -> stored prompt=%r -> %s (%d bytes)", prompt[:60], path.name, len(data))
        return str(path)
