"""In-memory preview cache for the two-step upload workflow.

Stores parsed KML data between the preview and confirm steps.
Entries expire after 15 minutes.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field

from .models import SurveyPoint

logger = logging.getLogger(__name__)

TTL_SECONDS = 15 * 60  # 15 minutes


@dataclass
class PreviewFileData:
    """Cached data for a single file in a preview."""

    filename: str
    content: bytes
    content_type: str
    kml_text: str
    points: list[SurveyPoint]
    file_id: str = ""
    layer_names: tuple[str, ...] = ()


@dataclass
class PreviewEntry:
    """A cached preview result."""

    files: list[PreviewFileData]
    user_email: str
    created_at: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > TTL_SECONDS


# Module-level cache
_cache: dict[str, PreviewEntry] = {}


def store_preview(files: list[PreviewFileData], user_email: str) -> str:
    """Store parsed preview data and return a preview_id."""
    _cleanup_expired()
    preview_id = str(uuid.uuid4())
    _cache[preview_id] = PreviewEntry(files=files, user_email=user_email)
    logger.info("Stored preview %s with %d file(s).", preview_id, len(files))
    return preview_id


def get_preview(preview_id: str) -> PreviewEntry | None:
    """Retrieve a preview entry, returning None if missing or expired."""
    entry = _cache.get(preview_id)
    if entry is None:
        return None
    if entry.is_expired:
        del _cache[preview_id]
        logger.info("Preview %s expired.", preview_id)
        return None
    return entry


def remove_preview(preview_id: str) -> None:
    """Remove a preview entry after it has been confirmed."""
    _cache.pop(preview_id, None)


def _cleanup_expired() -> None:
    """Remove all expired entries."""
    expired = [k for k, v in _cache.items() if v.is_expired]
    for k in expired:
        del _cache[k]
    if expired:
        logger.info("Cleaned up %d expired preview(s).", len(expired))
