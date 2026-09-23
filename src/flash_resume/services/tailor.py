"""Orchestrator that applies tailoring plans and compiles finalized artifacts."""

from __future__ import annotations

import copy
import re
import time
from pathlib import Path
from typing import Optional, Tuple

from flash_resume.config import AppConfig
from flash_resume.models.resume import MasterResume
from flash_resume.models.tailoring import TailorPlan, TailorResult
from flash_resume.services.compiler import CompilerService
from flash_resume.services.llm import LLMService
from flash_resume.services.validator import validate_bullet_length


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


def build_evidence_map(resume: MasterResume, job_description: str) -> tuple[list[str], list[str]]:
    """Return JD terms that are supported by the resume and unsupported JD terms."""
    resume_text = resume.model_dump_json().casefold()
    jd_text = job_description.casefold()
    jd_terms = [term for term in EVIDENCE_TERMS if term.casefold() in jd_text]
    supported = [term for term in jd_terms if term.casefold() in resume_text]
    unsupported = [term for term in jd_terms if term.casefold() not in resume_text]
    return supported, unsupported


def sanitize_tailor_plan(
    plan: TailorPlan,
    resume: MasterResume,
    job_description: str,
) -> TailorPlan:
    """Remove unsupported model claims and edits that violate layout constraints."""
    sanitized = copy.deepcopy(plan)
    supported_terms, unsupported_terms = build_evidence_map(resume, job_description)
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


class TailorEngine:
    """Main pipeline engine executing the full tailoring flow."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.compiler = CompilerService()
        self.llm = LLMService(
            api_key=config.resolve_api_key(),
            model=config.default_model,
        )

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

        # 2. Call LLM for Structured Tailor Plan
        llm_started_at = time.perf_counter()
        supported_terms, unsupported_terms = build_evidence_map(master_resume, job_description)
        plan = sanitize_tailor_plan(
            self.llm.generate_tailor_plan(
                resume=master_resume,
                job_description=job_description,
                company_override=company_override,
                role_override=role_override,
                supported_terms=supported_terms,
                unsupported_terms=unsupported_terms,
            ),
            master_resume,
            job_description,
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

        # 5. Compile PDF and Enforce 1-Page Layout
        pages, compile_ms = self.compiler.compile(
            resume=tailored_resume,
            output_pdf_path=pdf_path,
            max_pages=self.config.max_pages,
        )

        # 6. Save JSON & Diff Markdown
        json_path.write_text(tailored_resume.model_dump_json(indent=2), encoding="utf-8")
        diff_md = generate_diff_markdown(
            original=master_resume,
            tailored=tailored_resume,
            plan=plan,
            page_count=pages,
            compile_time_ms=compile_ms,
        )
        diff_path.write_text(diff_md, encoding="utf-8")

        return TailorResult(
            pdf_path=str(pdf_path.resolve()),
            json_path=str(json_path.resolve()),
            diff_path=str(diff_path.resolve()),
            page_count=pages,
            llm_time_ms=llm_time_ms,
            compile_time_ms=compile_ms,
            total_time_ms=(time.perf_counter() - started_at) * 1000,
            plan=plan,
        )

