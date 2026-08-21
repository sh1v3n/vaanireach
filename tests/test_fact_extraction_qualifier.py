"""FACT_EXTRACTION_PROMPT (providers/llm/groq_provider.py) must populate
the new "qualifier" field when a document contains multiple facts of the
same fact_type that need to stay distinguishable — the real-world trigger
being tabular/list source data, e.g. ugc_scholarship_circular.pdf's two
closing dates for two different applicant categories (bug report,
2026-08-21). This is a real Groq-call regression test, not a mock —
requires GROQ_API_KEYS/GROQ_API_KEY; skipped otherwise.
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
from core.models.enums import FactType  # noqa: E402
from providers.llm.groq_provider import GroqLLMProvider  # noqa: E402

_HAS_KEY = bool(os.environ.get("GROQ_API_KEYS") or os.environ.get("GROQ_API_KEY"))

# A minimal reproduction of the real ugc_scholarship_circular.pdf table
# that triggered this bug: two closing dates, for two different actors.
TABULAR_NOTICE = (
    "University Grants Commission — Post Matric Scholarship, 2021-22\n\n"
    "Important Dates:\n"
    "- Closing date for filing online applications by students: 30th November, 2021\n"
    "- Closing date for Verification of applications by Institutions: 15th December, 2021\n"
)


@pytest.mark.skipif(not _HAS_KEY, reason="GROQ_API_KEYS/GROQ_API_KEY not set")
def test_tabular_deadlines_get_distinct_qualifiers():
    page = DocumentPage(document_id="doc-tabular-test", page_number=1, raw_text=TABULAR_NOTICE)
    facts = GroqLLMProvider().extract_facts("doc-tabular-test", [page], project_id="proj-tabular-test")

    deadline_facts = [f for f in facts if f.fact_type == FactType.DEADLINE]
    assert len(deadline_facts) == 2, f"expected 2 distinct deadline facts, got {len(deadline_facts)}: {deadline_facts}"

    for fact in deadline_facts:
        assert fact.qualifier, f"deadline fact {fact.value!r} has no qualifier — the two dates will be indistinguishable"

    qualifiers = [f.qualifier.lower() for f in deadline_facts]
    assert qualifiers[0] != qualifiers[1], f"both deadline facts got the same qualifier: {qualifiers!r}"

    # Loosely confirm each qualifier actually names the right actor —
    # not asserting exact wording (the model phrases this freely), just
    # that "student" and "institution" land on the correct date.
    by_value = {f.value: f.qualifier.lower() for f in deadline_facts}
    student_deadline = next(v for v in by_value if "30" in v or "nov" in v.lower())
    institution_deadline = next(v for v in by_value if "15" in v or "dec" in v.lower())
    assert "student" in by_value[student_deadline]
    assert "institution" in by_value[institution_deadline]
