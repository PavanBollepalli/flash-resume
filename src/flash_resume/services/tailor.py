"""Orchestrator that applies tailoring plans and compiles finalized artifacts."""

from __future__ import annotations

import copy
import logging
import re
import time
from pathlib import Path
from typing import Optional, Tuple

import pypdf

from flash_resume.config import AppConfig
from flash_resume.models.resume import MasterResume
from flash_resume.models.tailoring import JDKeywords, TailorPlan, TailorResult
from flash_resume.services.compiler import CompilerService
from flash_resume.services.llm import LLMService
from flash_resume.services.validator import validate_bullet_length


logger = logging.getLogger("flash_resume.tailor")


EVIDENCE_TERMS = (
    "Python", "FastAPI", "REST API", "SQL", "PostgreSQL", "MySQL", "Docker", "Kubernetes",
    "AWS", "Google Cloud", "GCP", "Azure", "Azure OpenAI", "Azure Blob Storage", "Container Apps",
    "Git", "GitHub Actions", "Azure DevOps", "CI/CD", "Linux",
    "Java", "JavaScript", "TypeScript", "React", "Next.js", "Node.js", "Express.js",
    "Generative AI", "GenAI", "LLM", "RAG", "RAG pipelines", "retrieval-augmented generation",
    "LangChain", "Pydantic", "async Python", "structured output", "schema validation", "retry logic",
    "fallback handling", "document ingestion", "multi-step", "tool-using LLM agents", "PySpark",
    "prompt engineering", "vector embeddings", "FastAPI REST endpoints", "Streamlit",
    "pgvector", "Mistral", "ChromaDB", "O*NET", "data processing", "analytics pipelines",
    "object-oriented programming", "debugging", "unit tests", "Git/version control",
)


def sanitize_filename(name: str) -> str:
    """Sanitize a string for safe filesystem naming."""
    clean = re.sub(r"[^\w\s-]", "", name).strip()
    return re.sub(r"[-\s]+", "_", clean)


def apply_tailor_plan(resume: MasterResume, plan: TailorPlan) -> MasterResume:
    """Apply structured edits from a TailorPlan onto a MasterResume clone."""
    tailored = copy.deepcopy(resume)

    # 1. Update Skills
    for skill_up in plan.skill_updates:
        for cat in tailored.skills:
            if cat.category.strip().lower() == skill_up.category.strip().lower():
                # Merge updated items while preserving uniqueness
                seen = set()
                new_items = []
                for item in skill_up.updated_items:
                    if item.lower() not in seen:
                        seen.add(item.lower())
                        new_items.append(item)
                cat.items = new_items
                break

    # 2. Update Bullets in Experience and Projects
    for edit in plan.bullet_edits:
        if edit.action == "skip":
            continue
        is_valid, word_delta, width_ratio = validate_bullet_length(
            edit.original_text,
            edit.replacement_text,
        )
        if not is_valid:
            continue
        target_section = edit.section.strip().lower()
        if "exp" in target_section:
            for exp in tailored.experience:
                if exp.id == edit.item_id or not edit.item_id:
                    if 0 <= edit.bullet_index < len(exp.bullets):
                        exp.bullets[edit.bullet_index] = edit.replacement_text
                        break
        elif "proj" in target_section:
            for proj in tailored.projects:
                if proj.id == edit.item_id or not edit.item_id:
                    if 0 <= edit.bullet_index < len(proj.bullets):
                        proj.bullets[edit.bullet_index] = edit.replacement_text
                        break

    # 3. Update Summary if provided
    if plan.summary_edit and tailored.summary:
        tailored.summary = plan.summary_edit

    return tailored


def build_evidence_map(
    resume: MasterResume,
    job_description: str,
    jd_keywords: Optional[JDKeywords] = None,
) -> tuple[list[str], list[str]]:
    """Return JD terms that are supported by the resume and unsupported JD terms.

    When ``jd_keywords`` (LLM call #1) is provided, it is the source of
    candidate terms — bypassing the static EVIDENCE_TERMS list so niche or
    novel JD keywords (e.g. LLVM, compilers) are honored. Otherwise the static
    list is used (offline / dry-run fallback).
    """
    resume_text = resume.model_dump_json().casefold()
    if jd_keywords:
        candidates = jd_keywords.required_keywords + jd_keywords.preferred_keywords
    else:
        jd_text = job_description.casefold()
        candidates = [term for term in EVIDENCE_TERMS if term.casefold() in jd_text]
    supported = [term for term in candidates if term.casefold() in resume_text]
    unsupported = [term for term in candidates if term.casefold() not in resume_text]
    return supported, unsupported


def sanitize_tailor_plan(
    plan: TailorPlan,
    resume: MasterResume,
    job_description: str,
    jd_keywords: Optional[JDKeywords] = None,
) -> TailorPlan:
    """Remove unsupported model claims and edits that violate layout constraints."""
    sanitized = copy.deepcopy(plan)
    supported_terms, unsupported_terms = build_evidence_map(
        resume, job_description, jd_keywords
    )
    supported_lookup = {term.casefold() for term in supported_terms}
    resume_text = resume.model_dump_json().casefold()

    sanitized.matched_keywords = supported_terms
    sanitized.missing_keywords = unsupported_terms
    sanitized.ats_match_score = round(
        len(supported_terms) / max(len(supported_terms) + len(unsupported_terms), 1) * 100
    )
    for skill_update in sanitized.skill_updates:
        original_lookup = {original.casefold() for original in skill_update.original_items}
        skill_update.updated_items = [
            item for item in skill_update.updated_items
            if item.casefold() in original_lookup or item.casefold() in resume_text
        ]
        skill_update.added_keywords = [
            keyword for keyword in skill_update.added_keywords
            if keyword.casefold() in supported_lookup
        ]
    sanitized.bullet_edits = [
        edit
        for edit in sanitized.bullet_edits
        if edit.action != "skip"
        and validate_bullet_length(edit.original_text, edit.replacement_text)[0]
        and any(
            term.casefold() in edit.replacement_text.casefold()
            and term.casefold() not in edit.original_text.casefold()
            for term in supported_terms
        )
    ]
    return sanitized


def generate_diff_markdown(
    original: MasterResume,
    tailored: MasterResume,
    plan: TailorPlan,
    page_count: int,
    compile_time_ms: float,
) -> str:
    """Generate a clean Markdown diff report of all modifications."""
    lines = [
        f"# Flash Resume ATS Tailoring Report: {plan.company} - {plan.role}",
        "",
        f"- **JD Coverage:** {len(plan.matched_keywords)}/{len(plan.matched_keywords) + len(plan.missing_keywords)} supported requirements",
        f"- **Applied Bullet Edits:** {len(plan.bullet_edits)}",
        f"- **Page Count:** {page_count} (Verified Single-Page Fit)",
        f"- **Typst Compile Latency:** {compile_time_ms:.1f} ms",
        f"- **Integrated Keywords:** {', '.join(plan.matched_keywords) if plan.matched_keywords else 'None'}",
        "",
        "## Modified Bullet Points",
        "",
        "| Section | Original Bullet | Tailored Bullet (ATS Optimized) | Word Δ |",
        "| :--- | :--- | :--- | :--- |",
    ]

    for edit in plan.bullet_edits:
        orig_words = len(edit.original_text.split())
        new_words = len(edit.replacement_text.split())
        delta = new_words - orig_words
        sign = f"+{delta}" if delta > 0 else str(delta)
        lines.append(
            f"| **{edit.section}** | {edit.original_text} | {edit.replacement_text} | `{sign}` words |"
        )

    lines.extend([
        "",
        "## Skills Adjustments",
        "",
    ])

    for skill_up in plan.skill_updates:
        if skill_up.added_keywords:
            lines.append(
                f"- **{skill_up.category}:** Added `{', '.join(skill_up.added_keywords)}`"
            )

    return "\n".join(lines) + "\n"


def _drop_one_unit(resume: MasterResume, aggressive: bool = False) -> MasterResume:
    """Remove one low-value content unit to reclaim vertical space.

    ``aggressive=True`` is used for severe overflow (3+ pages): whole entries
    are dropped instead of nibbling single bullets, so the loop converges in a
    handful of passes instead of dozens. Entries are dropped from the end of
    their lists, which holds the oldest (lowest-value) items first.
    """
    trimmed = copy.deepcopy(resume)

    if aggressive:
        if len(trimmed.experience) > 1:
            trimmed.experience.pop()
            return trimmed
        if len(trimmed.projects) > 1:
            trimmed.projects.pop()
            return trimmed

    # 1. Drop last certification
    if trimmed.certifications:
        trimmed.certifications.pop()
        return trimmed

    # 2. Drop last award
    if trimmed.awards:
        trimmed.awards.pop()
        return trimmed

    # 3. Trim the longest project down to a minimum of 3 bullets
    if trimmed.projects:
        longest_proj = max(trimmed.projects, key=lambda p: len(p.bullets))
        if len(longest_proj.bullets) > 3:
            longest_proj.bullets.pop()
            return trimmed

    # 4. Trim the oldest experience entry down to a minimum of 2 bullets
    if trimmed.experience:
        oldest_exp = trimmed.experience[-1]
        if len(oldest_exp.bullets) > 2:
            oldest_exp.bullets.pop()
            return trimmed

    # 5. Drop the oldest project entirely
    if len(trimmed.projects) > 1:
        trimmed.projects.pop()
        return trimmed

    # 6. Drop the oldest experience entry entirely
    if len(trimmed.experience) > 1:
        trimmed.experience.pop()
        return trimmed

    # 7. Drain any remaining bullets (projects first, then experience)
    if trimmed.projects:
        longest_proj = max(trimmed.projects, key=lambda p: len(p.bullets))
        if longest_proj.bullets:
            longest_proj.bullets.pop()
            return trimmed
    if trimmed.experience and trimmed.experience[-1].bullets:
        trimmed.experience[-1].bullets.pop()
        return trimmed

    # 8. Drop older education entries (keep the first/highest degree listed)
    if len(trimmed.education) > 1:
        trimmed.education.pop()
        return trimmed

    # 9. Truncate summary to 1 sentence
    if trimmed.summary:
        sentences = trimmed.summary.replace(". ", ".\x00").split("\x00")
        if len(sentences) > 1:
            trimmed.summary = sentences[0].rstrip(".")
            return trimmed

    # Nothing left to trim — return as-is
    return trimmed


MAX_CONDENSE_ATTEMPTS = 2


def extract_overflow(pdf_path: Path, max_pages: int) -> tuple[str, int]:
    """Return (spilled_text, spilled_word_count) from pages beyond max_pages.

    Never raises — on any extraction failure returns ("", 0) and the caller
    falls back to a blind condensation pass.
    """
    try:
        reader = pypdf.PdfReader(str(pdf_path))
        text = "\n".join(
            reader.pages[i].extract_text() or ""
            for i in range(max_pages, len(reader.pages))
        ).strip()
        return text, len(text.split())
    except Exception:
        return "", 0


def trim_resume_to_fit(
    resume: MasterResume,
    compiler: "CompilerService",
    output_pdf_path: Path,
    max_pages: int = 1,
    max_iterations: int = 40,
    llm=None,
    job_description: str = "",
) -> tuple[MasterResume, int, float, bool]:
    """Shrink an overflowing resume until it fits the page budget.

    Strategy, in order:
    1. Density fallback (inside ``compiler.compile``): standard → compact →
       tight. Free — no content is lost.
    2. LLM condensation (when ``llm`` is provided): up to two model calls,
       each seeded with the exact text measured on the overflow page(s) and
       the word target to reclaim, rewriting bullets tighter while preserving
       metrics.
    3. Deterministic drop loop: removes one low-value unit per pass and
       recompiles (~30ms each). Also the only path when no LLM is available
       (dry-run, offline tests) or the condensation call fails.

    Returns:
        Tuple of (fitted_resume, page_count, total_compile_ms, trim_applied)
    """
    current = resume
    total_ms = 0.0

    # First pass — the compiler applies density fallback internally
    pages, ms = compiler.compile(current, output_pdf_path, max_pages)
    total_ms += ms
    if pages <= max_pages:
        return current, pages, total_ms, False

    # Ask the LLM to condense the overflow before dropping anything by rule.
    # Each attempt measures what actually spilled onto the overflow page(s)
    # and hands the LLM an exact word target, so the rewrite is surgical
    # instead of blind.
    if llm is not None:
        for attempt in range(MAX_CONDENSE_ATTEMPTS):
            overflow_text, overflow_words = extract_overflow(output_pdf_path, max_pages)
            try:
                condensed = llm.condense_resume(
                    current,
                    job_description,
                    max_pages,
                    overflow_text=overflow_text,
                    overflow_words=overflow_words,
                )
                condensed_pages, ms = compiler.compile(condensed, output_pdf_path, max_pages)
                total_ms += ms
                logger.info(
                    "LLM condensation pass %d | pages=%d | overflow_words=%d",
                    attempt + 1,
                    condensed_pages,
                    overflow_words,
                )
                if condensed_pages <= max_pages:
                    return condensed, condensed_pages, total_ms, True
                # Keep the rewrite only if it actually reduced the overflow
                if condensed_pages < pages:
                    current, pages = condensed, condensed_pages
                else:
                    break  # rewrite made no progress — don't waste another call
            except Exception as exc:
                logger.warning(
                    "LLM condensation failed (%s); falling back to rule-based trimming", exc
                )
                break

    # Still overflowing — drop one unit per pass until it fits
    for _ in range(max_iterations):
        current = _drop_one_unit(current, aggressive=pages > max_pages + 1)
        pages, ms = compiler.compile(current, output_pdf_path, max_pages)
        total_ms += ms
        if pages <= max_pages:
            return current, pages, total_ms, True

    # Last resort: return what we have (shouldn't happen in practice)
    return current, pages, total_ms, True


class TailorEngine:
    """Main pipeline engine executing the full tailoring flow."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.compiler = CompilerService()
        if config.llm_provider == "groq":
            from flash_resume.services.groq_llm import GroqLLMService

            self.llm = GroqLLMService(
                api_key=config.resolve_groq_api_key(),
                model=config.groq_model,
            )
        else:
            self.llm = LLMService(
                api_key=config.resolve_api_key(),
                model=config.default_model,
            )
        self.model = config.groq_model if config.llm_provider == "groq" else config.default_model

    def tailor(
        self,
        job_description: str,
        master_resume: Optional[MasterResume] = None,
        company_override: Optional[str] = None,
        role_override: Optional[str] = None,
        output_dir_override: Optional[str] = None,
    ) -> TailorResult:
        """Run the end-to-end tailoring and compilation pipeline."""
        started_at = time.perf_counter()
        # 1. Load Master Resume
        if master_resume is None:
            if not self.config.master_resume_path:
                raise ValueError("No master resume configured. Run 'fs init' first.")
            resume_path = Path(self.config.master_resume_path)
            if not resume_path.exists():
                raise FileNotFoundError(f"Master resume not found at {resume_path}")
            master_resume = MasterResume.model_validate_json(resume_path.read_text(encoding="utf-8"))

        logger.info(
            "Tailor started | provider=%s | model=%s",
            self.config.llm_provider,
            self.model,
        )

        # 2. Call LLM #1 to extract ATS keywords, then LLM #2 for the plan.
        llm_started_at = time.perf_counter()
        jd_keywords = self.llm.extract_jd_keywords(job_description)
        supported_terms, unsupported_terms = build_evidence_map(
            master_resume, job_description, jd_keywords
        )
        plan = sanitize_tailor_plan(
            self.llm.generate_tailor_plan(
                resume=master_resume,
                job_description=job_description,
                company_override=company_override,
                role_override=role_override,
                jd_keywords=jd_keywords,
                supported_terms=supported_terms,
                unsupported_terms=unsupported_terms,
            ),
            master_resume,
            job_description,
            jd_keywords,
        )
        llm_time_ms = (time.perf_counter() - llm_started_at) * 1000

        # 3. Apply Plan to Clone
        tailored_resume = apply_tailor_plan(master_resume, plan)

        # 4. Resolve Output Paths
        out_base = Path(output_dir_override or self.config.output_dir)
        out_base.mkdir(parents=True, exist_ok=True)

        prefix = f"{sanitize_filename(plan.company)}_{sanitize_filename(plan.role)}"
        pdf_path = out_base / f"{prefix}.pdf"
        json_path = out_base / f"{prefix}.json"
        diff_path = out_base / f"{prefix}.diff.md"

        # 5. Compile PDF and Enforce 1-Page Layout. Density fallback runs
        # inside the compiler; if that still overflows, the LLM condenses the
        # content, with a deterministic drop loop as the final backstop.
        tailored_resume, pages, compile_ms, trim_applied = trim_resume_to_fit(
            resume=tailored_resume,
            compiler=self.compiler,
            output_pdf_path=pdf_path,
            max_pages=self.config.max_pages,
            llm=self.llm,
            job_description=job_description,
        )

        # 6. Save JSON & Diff Markdown (after trimming so JSON matches the PDF)
        json_path.write_text(tailored_resume.model_dump_json(indent=2), encoding="utf-8")
        diff_md = generate_diff_markdown(
            original=master_resume,
            tailored=tailored_resume,
            plan=plan,
            page_count=pages,
            compile_time_ms=compile_ms,
        )
        diff_path.write_text(diff_md, encoding="utf-8")

        total_time_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "Tailor finished | provider=%s | model=%s | llm=%.0fms | compile=%.0fms | total=%.0fms | pages=%d",
            self.config.llm_provider,
            self.model,
            llm_time_ms,
            compile_ms,
            total_time_ms,
            pages,
        )

        return TailorResult(
            pdf_path=str(pdf_path.resolve()),
            json_path=str(json_path.resolve()),
            diff_path=str(diff_path.resolve()),
            page_count=pages,
            llm_time_ms=llm_time_ms,
            compile_time_ms=compile_ms,
            total_time_ms=total_time_ms,
            plan=plan,
            trim_applied=trim_applied,
        )

