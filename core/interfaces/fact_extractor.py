"""FactExtractor — turns parsed DocumentPages into SourceFacts. This is
what populates the Source Fact Ledger; every fact it returns must carry a
SourceSpan pointing at exactly where it came from."""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.document import DocumentPage
from core.models.enums import FactType
from core.models.fact import SourceFact


class FactExtractor(ABC):
    @abstractmethod
    def supported_fact_types(self) -> list[FactType]:
        raise NotImplementedError(
            "FactExtractor.supported_fact_types not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def extract_facts(self, document_id: str, pages: list[DocumentPage]) -> list[SourceFact]:
        raise NotImplementedError(
            "FactExtractor.extract_facts not implemented — Phase 0 interface stub"
        )
