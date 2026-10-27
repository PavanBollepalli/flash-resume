"""Hybrid provider — the fast model takes the one stage it measurably wins.

A "hybrid" pipeline sounds like it should spread the work across both
providers, but *which* stages move is an empirical question, and the answer
turned out to be much narrower than the intuition suggests. Both candidate
moves were measured:

* **Planning moved to Groq is 4.4x SLOWER.** ``gpt-oss-20b`` is quick on small
  structured lookups; asked to rewrite a full resume's summary, skills and
  experience against a schema it took **47.8s against Gemini's 10.9s** on the
  same posting.

* **Extraction moved to Groq is a clear win.** 6.1s down to 1.4s, and on the
  real posting its requirements were the *more* faithful of the two — 13 of 15
  kept the posting's qualifier ("years", "production", "scalable") against 3 of
  18 for Gemini — so routing extraction does not quietly make requirements
  easier to satisfy.

So exactly one stage moves. That is the honest shape of this architecture, and
it is worth roughly a fifth of the run, not the several-fold speedup the
"hybrid" name suggests: the expensive stage is expensive for reasons a provider
swap does not fix. The figures above come from the benchmark run recorded on the
``feature-99`` branch (``scripts/bench_*``, not merged here).

Scoring is not a stage at all — it is a local string match against the final
resume — which is why the pipeline is one extraction call plus two parallel
planning calls and nothing else.

Every fast-path call falls back to the reasoning provider, so a Groq outage
costs latency rather than a failed run, and because extraction feeds the
scorer, falling back is what keeps a bad extraction from silently becoming a
wrong score.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from flash_resume.models.resume import MasterResume
from flash_resume.models.tailoring import (
    CoreEditPlan,
    JDKeywords,
    ProjectEditPlan,
    TailorPlan,
)
from flash_resume.services.edit_contract import BulletTarget

logger = logging.getLogger("flash_resume.hybrid")


def _is_empty(kws: JDKeywords) -> bool:
    """Report whether an extraction returned nothing usable.

    ``GroqLLMService.extract_jd_keywords`` reports failure by returning an empty
    ``JDKeywords`` rather than raising, so an empty result is a failure to fall
    back from — not a job description with no requirements in it.

    Only the two keyword lists count: generic soft skills are derived locally by
    ``soft_skill_labels`` and never feed the scorer, so they could not make an
    otherwise-empty extraction usable.
    """
    return not (kws.required_keywords or kws.preferred_keywords)


class HybridLLMService:
    """Routes each pipeline stage to the provider that should actually run it.

    Extraction goes to ``fast`` (Groq). Planning, condensation and resume
    ingestion go to ``reasoning`` (Gemini). Same method surface as
    ``LLMService``, so the engine, the split-plan runner and ``fs import`` all
    use it without knowing a second provider exists.
    """

    def __init__(self, fast, reasoning):
        self.fast = fast
        self.reasoning = reasoning
        self.model = f"{reasoning.model} + {fast.model} (extract)"

    # -- fast path, with a fallback that protects the score ------------------

    def extract_jd_keywords(self, job_description: str) -> JDKeywords:
        """Extract ATS keywords on the fast provider, falling back on emptiness.

        Extraction is the input to scoring, so an empty or failed extraction
        must not become "this posting has no requirements". Falling back keeps a
        Groq hiccup at the cost of a slower run rather than a wrong one.
        """
        try:
            keywords = self.fast.extract_jd_keywords(job_description)
        except Exception as exc:
            logger.warning("Fast extraction failed (%s); using the reasoning provider.", exc)
            return self.reasoning.extract_jd_keywords(job_description)

        if _is_empty(keywords):
            logger.warning("Fast extraction returned no requirements; using the reasoning provider.")
            return self.reasoning.extract_jd_keywords(job_description)
        return keywords

    # -- reasoning path ------------------------------------------------------
    #
    # Both tailoring calls go to the reasoning provider: they rewrite the
    # candidate's own words, and Groq measured 4.4x the latency for the same
    # work. See the module docstring.

    def generate_core_edit_plan(
        self, resume: MasterResume, job_description: str,
        company_override: Optional[str] = None, role_override: Optional[str] = None,
        supported_terms: Optional[list[str]] = None,
        unsupported_terms: Optional[list[str]] = None,
        jd_keywords: Optional[JDKeywords] = None, interview_mode: bool = False,
        targets: Optional[Sequence[BulletTarget]] = None,
    ) -> CoreEditPlan:
        return self.reasoning.generate_core_edit_plan(
            resume=resume,
            job_description=job_description,
            company_override=company_override,
            role_override=role_override,
            supported_terms=supported_terms,
            unsupported_terms=unsupported_terms,
            jd_keywords=jd_keywords,
            interview_mode=interview_mode,
            targets=targets,
        )

    def generate_project_edit_plan(
        self, resume: MasterResume, job_description: str,
        company_override: Optional[str] = None, role_override: Optional[str] = None,
        supported_terms: Optional[list[str]] = None,
        unsupported_terms: Optional[list[str]] = None,
        jd_keywords: Optional[JDKeywords] = None,
        targets: Optional[Sequence[BulletTarget]] = None,
    ) -> ProjectEditPlan:
        return self.reasoning.generate_project_edit_plan(
            resume=resume,
            job_description=job_description,
            company_override=company_override,
            role_override=role_override,
            supported_terms=supported_terms,
            unsupported_terms=unsupported_terms,
            jd_keywords=jd_keywords,
            targets=targets,
        )

    def generate_tailor_plan(
        self, resume: MasterResume, job_description: str,
        company_override: Optional[str] = None, role_override: Optional[str] = None,
        supported_terms: Optional[list[str]] = None,
        unsupported_terms: Optional[list[str]] = None,
        jd_keywords: Optional[JDKeywords] = None, interview_mode: bool = False,
        targets: Optional[Sequence[BulletTarget]] = None,
    ) -> TailorPlan:
        """The legacy single-call plan, used only when both specialists fail."""
        return self.reasoning.generate_tailor_plan(
            resume=resume,
            job_description=job_description,
            company_override=company_override,
            role_override=role_override,
            supported_terms=supported_terms,
            unsupported_terms=unsupported_terms,
            jd_keywords=jd_keywords,
            interview_mode=interview_mode,
            targets=targets,
        )

    def condense_resume(
        self, resume: MasterResume, job_description: str = "", max_pages: int = 1,
        overflow_text: str = "", overflow_words: int = 0,
    ) -> MasterResume:
        """Condense on the reasoning provider.

        Condensation rewrites real content under a factual-integrity rule, and
        nothing downstream verifies that a dropped metric was dropped honestly —
        the page-count check only confirms it got shorter. It stays with the
        stronger writer.
        """
        return self.reasoning.condense_resume(
            resume=resume,
            job_description=job_description,
            max_pages=max_pages,
            overflow_text=overflow_text,
            overflow_words=overflow_words,
        )

    def parse_resume_from_text(self, raw_text: str) -> MasterResume:
        """Ingest on the reasoning provider — this writes the master resume."""
        return self.reasoning.parse_resume_from_text(raw_text)
