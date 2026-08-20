"""File type/size validation for document uploads.

These checks are cheap, provider-independent, and useful from day one —
unlike the pipeline stages, they don't depend on any undecided vendor, so
they're implemented for real in Phase 0 rather than left as an interface.
"""
from __future__ import annotations

ALLOWED_MIME_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


def validate_file_type(filename: str, mime_type: str, allowed_extensions: list[str]) -> None:
    """Raises ValueError if the file's extension/MIME type is not allowed.

    Checks both extension and declared MIME type — neither is trustworthy
    alone (an uploader can rename a file or spoof a Content-Type), so this
    is a first line of defense, not a substitute for real content sniffing
    if that's ever needed.
    """
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in allowed_extensions:
        raise ValueError(f"File type '.{extension}' is not allowed. Allowed: {allowed_extensions}")

    expected_mime = ALLOWED_MIME_TYPES.get(extension)
    if expected_mime is not None and mime_type != expected_mime:
        raise ValueError(
            f"MIME type '{mime_type}' does not match expected type for '.{extension}' "
            f"({expected_mime})"
        )


def validate_file_size(size_bytes: int, max_mb: int) -> None:
    """Raises ValueError if the file exceeds the configured size limit."""
    max_bytes = max_mb * 1024 * 1024
    if size_bytes > max_bytes:
        raise ValueError(f"File size {size_bytes} bytes exceeds the {max_mb}MB limit")
    if size_bytes <= 0:
        raise ValueError("File is empty")
