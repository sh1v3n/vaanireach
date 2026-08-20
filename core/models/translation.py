"""Translation — a script's narration rendered into one target language.
Still subordinate to the Source Fact Ledger: translated claims are
re-verified, not assumed correct because the English claim passed."""
from __future__ import annotations

from core.models.base import IdentifiedModel
from core.models.enums import LanguageCode, TranslationStatus


class Translation(IdentifiedModel):
    project_id: str
    script_id: str
    language: LanguageCode
    translated_narration_text: str
    translation_provider: str
    status: TranslationStatus = TranslationStatus.PENDING
