"""FACT_EXTRACTION_PROMPT (providers/llm/groq_provider.py, mirrored in
gemini_provider.py) must normalize every fact's "value" to English
regardless of the source document's language — the Source Fact Ledger is
the language-neutral input every target-language script (including an
English one) gets generated from. Before this fix, a Marathi source
document produced Marathi-language fact values for text-based fact types
(scheme/eligibility/requirement), which then leaked verbatim into
generated ENGLISH narration (confirmed live, 2026-08-21: an EN scene
literally read "Introducing मोफत पाठ्यपुस्तक वाटप योजना."). This is a
real Groq-call regression test, not a mock — requires GROQ_API_KEYS/
GROQ_API_KEY; skipped otherwise.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from core.models.document import DocumentPage  # noqa: E402
from providers.llm.groq_provider import GroqLLMProvider  # noqa: E402

_HAS_KEY = bool(os.environ.get("GROQ_API_KEYS") or os.environ.get("GROQ_API_KEY"))

MARATHI_NOTICE = (
    "महाराष्ट्र शासन, शालेय शिक्षण विभाग यांच्याकडून मोफत पाठ्यपुस्तक वाटप योजना जाहीर "
    "करण्यात आली आहे. इयत्ता १ ली ते ८ वी मधील सर्व सरकारी व अनुदानित शाळांमधील विद्यार्थी "
    "या योजनेसाठी पात्र आहेत. प्रत्येक पात्र विद्यार्थ्याला १२०० रुपये किमतीची पाठ्यपुस्तके "
    "मोफत मिळतील. पालकांनी नोंदणी फॉर्म ३० सप्टेंबर २०२६ पर्यंत शाळेत जमा करावा."
)


def _has_devanagari(text: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in text)


@pytest.mark.skipif(not _HAS_KEY, reason="GROQ_API_KEYS/GROQ_API_KEY not set")
def test_marathi_source_document_produces_english_fact_values():
    page = DocumentPage(document_id="doc-mr-test", page_number=1, raw_text=MARATHI_NOTICE)
    facts = GroqLLMProvider().extract_facts("doc-mr-test", [page], project_id="proj-mr-test")

    assert len(facts) >= 4, f"expected at least 4 facts extracted from the Marathi notice, got {len(facts)}"

    for fact in facts:
        assert not _has_devanagari(fact.value), (
            f"fact_type={fact.fact_type.value!r} value={fact.value!r} still contains Devanagari script — "
            "the Source Fact Ledger must be language-neutral (English) regardless of source-document language, "
            "since every target-language script (including English) is generated from these values"
        )
        # Provenance must stay verbatim in the ORIGINAL source language — this is
        # the opposite assertion from the one above, guarding against a lazy fix
        # that translates raw_text too (which would break citation/quoting).
        assert _has_devanagari(fact.raw_text), (
            f"fact_type={fact.fact_type.value!r} raw_text={fact.raw_text!r} lost its original Marathi text — "
            "raw_text is provenance and must stay verbatim in the source language, only value gets translated"
        )
