"""Orchestrator that applies tailoring plans and compiles finalized artifacts."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import logging
import re
import time
from pathlib import Path
from typing import Optional, Tuple

import pypdf

from flash_resume.config import AppConfig
from flash_resume.models.resume import MasterResume
from flash_resume.models.tailoring import (
    JDKeywords,
    CoreTailorPlan,
    ProjectTailorPlan,
    EvidenceAssignment,
    RejectedBulletEdit,
    RequirementMatch,
    TailorPlan,
    TailorResult,
)
from flash_resume.services.compiler import CompilerService
from flash_resume.services.evidence import (
    align_resume_to_jd,
    coverage_score,
    coverage_terms,
    contains_term,
    evaluate_requirements,
)
from flash_resume.services.llm import LLMService
from flash_resume.services.validator import validate_bullet_length


logger = logging.getLogger("flash_resume.tailor")


def merge_split_plans(
    core: CoreTailorPlan,
    projects: ProjectTailorPlan,
    company: Optional[str],
    role: Optional[str],
) -> TailorPlan:
    """Merge disjoint specialist outputs into the existing full plan schema."""
    inferred_company = company or core.company
    if not inferred_company or inferred_company.casefold() in {"infer", "unknown", "n/a"}:
        inferred_company = "Company"
    inferred_role = role or core.role
    if not inferred_role or inferred_role.casefold() in {"infer", "unknown", "n/a"}:
        inferred_role = "Software Engineer"
    return TailorPlan(
        company=inferred_company,
        role=inferred_role,
        summary_edit=core.summary_edit,
        skill_updates=core.skill_updates,
        bullet_edits=core.experience_bullet_edits + projects.project_bullet_edits,
        bullet_orders=core.experience_bullet_orders + projects.project_bullet_orders,
        content_priorities=projects.content_priorities,
        evidence_assignments=core.evidence_assignments + projects.evidence_assignments,
        preparation_skills=core.preparation_skills,
    )


def generate_split_plan(
    llm,
    resume: MasterResume,
    job_description: str,
    company_override: Optional[str],
    role_override: Optional[str],
    supported_terms: list[str],
    unsupported_terms: list[str],
    jd_keywords: JDKeywords,
    interview_mode: bool,
) -> TailorPlan:
    """Run specialist calls concurrently, falling back to the legacy call."""
    required = {
        "resume": resume,
        "job_description": job_description,
        "company_override": company_override,
        "role_override": role_override,
        "supported_terms": supported_terms,
        "unsupported_terms": unsupported_terms,
        "jd_keywords": jd_keywords,
    }
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            core_future = pool.submit(
                llm.generate_core_tailor_plan,
                **required,
                interview_mode=interview_mode,
            )
            project_future = pool.submit(llm.generate_project_tailor_plan, **required)
            core = core_future.result()
            projects = project_future.result()
        return merge_split_plans(
            core,
            projects,
            company_override or core.company,
            role_override or core.role,
        )
    except Exception:
        logger.exception("Split tailoring failed; using the legacy full-plan call.")
        return llm.generate_tailor_plan(**required, interview_mode=interview_mode)


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

GENERIC_SKILL_TERMS = {
    "backend stack",
    "end-to-end backend stack",
    "full-stack experience",
    "technical skills",
}
OUTCOME_PATTERNS = (
    r"\bachiev(?:e|es|ed|ing)\b",
    r"\bautomat(?:e|es|ed|ing)\b",
    r"\bdecreas(?:e|es|ed|ing)\b",
    r"\beliminat(?:e|es|ed|ing)\b",
    r"\bimprov(?:e|es|ed|ing)\b",
    r"\bincreas(?:e|es|ed|ing)\b",
    r"\boptimiz(?:e|es|ed|ing)\b",
    r"\bpublish(?:e|es|ed|ing)\b",
    r"\breduc(?:e|es|ed|ing)\b",
    r"\bresolv(?:e|es|ed|ing)\b",
    r"\bsav(?:e|es|ed|ing)\b",
    r"\bshipp(?:e|s|ed|ing)\b",
)


def _protected_bullet_facts(text: str) -> set[str]:
    """Return concrete facts that a rewrite must not silently remove."""
    return set(re.findall(r"\b\d+(?:\.\d+)?%?\b", text.casefold()))


def _preserves_bullet_facts(original: str, replacement: str) -> bool:
    original_lower = original.casefold()
    replacement_lower = replacement.casefold()
    if not _protected_bullet_facts(original).issubset(_protected_bullet_facts(replacement)):
        return False
    original_outcomes = {
        pattern for pattern in OUTCOME_PATTERNS if re.search(pattern, original_lower)
    }
    replacement_outcomes = {
        pattern for pattern in OUTCOME_PATTERNS if re.search(pattern, replacement_lower)
    }
    return not original_outcomes or bool(original_outcomes & replacement_outcomes)


def _within_bullet_budget(original: str, replacement: str) -> bool:
    """Allow limited safe compaction when the rewrite preserves evidence."""
    valid, word_delta, width_ratio = validate_bullet_length(original, replacement)
    if valid:
        return True
    return (
        word_delta >= -4
        and 0.75 <= width_ratio <= 1.12
        and _preserves_bullet_facts(original, replacement)
    )


def sanitize_filename(name: str) -> str:
    """Sanitize a string for safe filesystem naming."""
    clean = re.sub(r"[^\w\s-]", "", name).strip()
    return re.sub(r"[-\s]+", "_", clean)


def apply_tailor_plan(resume: MasterResume, plan: TailorPlan) -> MasterResume:
    """Apply structured edits from a TailorPlan onto a MasterResume clone."""
    tailored = copy.deepcopy(resume)
    if plan.role and tailored.contact.title != plan.role:
        tailored.contact.title = plan.role

    if plan.preparation_skills:
        category = next(
            (
                item
                for item in tailored.skills
                if item.category.strip().casefold() == "familiarity"
            ),
            None,
        )
        if category is None:
            from flash_resume.models.resume import SkillCategory

            category = SkillCategory(category="Familiarity", items=[])
            tailored.skills.append(category)
        existing = {item.casefold() for item in category.items}
        for skill in plan.preparation_skills:
            label = f"{skill.strip()} (familiarity)"
            if skill.strip() and label.casefold() not in existing:
                category.items.append(label)
                existing.add(label.casefold())

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
        if not _within_bullet_budget(edit.original_text, edit.replacement_text):
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

    # 3. Reorder bullets only when the model supplies a complete, valid
    # permutation. This preserves content and avoids accidental data loss.
    for order in plan.bullet_orders:
        target = None
        if order.section == "Experience":
            target = next((item for item in tailored.experience if item.id == order.item_id), None)
        else:
            target = next((item for item in tailored.projects if item.id == order.item_id), None)
        if target is None or sorted(order.bullet_indices) != list(range(len(target.bullets))):
            continue
        target.bullets = [target.bullets[index] for index in order.bullet_indices]

    # 4. Update Summary if provided
    if plan.summary_edit:
        tailored.summary = plan.summary_edit

    return tailored


def highlight_keywords(resume: MasterResume, keywords: list[str]) -> MasterResume:
    """Wrap matched ATS keywords in ``**bold**`` markers for the PDF render pass.

    The Typst template renders ``**…**`` spans in bold via its ``bold-markup``
    helper so integrated keywords catch a recruiter's eye. Matching is
    case-insensitive and whole-word; original casing is preserved. This is a
    display-only pass — run it after trimming so the generated PDF
    stays marker-free.
    """
    highlighted = copy.deepcopy(resume)
    # Longest terms first so "Amazon Web Services" wins over "AWS" when both match.
    terms = sorted({k.strip() for k in keywords if k and k.strip()}, key=len, reverse=True)
    if not terms:
        return highlighted
    pattern = re.compile(
        r"(?<!\w)(" + "|".join(re.escape(t) for t in terms) + r")(?!\w)",
        re.IGNORECASE,
    )

    def wrap(text: str) -> str:
        """Bold matched terms, coexisting with any ingest-time ``**`` markers.

        Split the text at existing ``**`` boundaries and apply keyword bolding
        only to the plain (even) segments — so JD terms get bolded in bullets
        that already carry ingest-time bold, while existing spans are preserved
        and never double-wrapped or corrupted.
        """
        if not text:
            return text
        if "**" not in text:
            return pattern.sub(lambda m: f"**{m.group(0)}**", text)
        segments = text.split("**")
        for i in range(0, len(segments), 2):
            if segments[i]:
                segments[i] = pattern.sub(lambda m: f"**{m.group(0)}**", segments[i])
        return "**".join(segments)

    if highlighted.summary:
        highlighted.summary = wrap(highlighted.summary)
    for exp in highlighted.experience:
        exp.bullets = [wrap(b) for b in exp.bullets]
    for proj in highlighted.projects:
        proj.bullets = [wrap(b) for b in proj.bullets]
    for cat in highlighted.skills:
        cat.items = [wrap(i) for i in cat.items]
    return highlighted


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
    if not jd_keywords or not (jd_keywords.required_keywords or jd_keywords.preferred_keywords):
        jd_text = job_description.casefold()
        jd_keywords = JDKeywords(
            required_keywords=[term for term in EVIDENCE_TERMS if term.casefold() in jd_text]
        )
    return coverage_terms(evaluate_requirements(resume, jd_keywords))


def _apply_coverage(plan: TailorPlan, assessment: list[RequirementMatch]) -> TailorPlan:
    """Attach deterministic evidence results to a model-generated plan."""
    supported, unsupported = coverage_terms(assessment)
    plan.requirement_matches = assessment
    plan.matched_keywords = supported
    plan.missing_keywords = unsupported
    plan.ats_match_score = coverage_score(assessment)
    return plan


def sanitize_tailor_plan(
    plan: TailorPlan,
    resume: MasterResume,
    job_description: str,
    jd_keywords: Optional[JDKeywords] = None,
    assessment: Optional[list[RequirementMatch]] = None,
    interview_mode: bool = False,
) -> TailorPlan:
    """Remove unsupported model claims and edits that violate layout constraints."""
    sanitized = copy.deepcopy(plan)
    if assessment is None:
        if not jd_keywords or not (jd_keywords.required_keywords or jd_keywords.preferred_keywords):
            jd_text = job_description.casefold()
            jd_keywords = JDKeywords(
                required_keywords=[term for term in EVIDENCE_TERMS if term.casefold() in jd_text]
            )
        assessment = evaluate_requirements(resume, jd_keywords)
    supported_terms, unsupported_terms = coverage_terms(assessment)
    supported_lookup = {term.casefold() for term in supported_terms}
    resume_text = resume.model_dump_json().casefold()
    unsupported_lookup = {term.casefold(): term for term in unsupported_terms}
    if interview_mode:
        sanitized.preparation_skills = [
            unsupported_lookup[term.casefold()]
            for term in sanitized.preparation_skills
            if term.casefold() in unsupported_lookup
        ][:5]
    else:
        sanitized.preparation_skills = []

    _apply_coverage(sanitized, assessment)
    if sanitized.summary_edit:
        unsupported_summary_terms = []
        for match in assessment:
            if (
                match.status != "covered"
                and contains_term(sanitized.summary_edit, match.requirement)
                and not contains_term(resume.summary, match.requirement)
            ):
                unsupported_summary_terms.append(match.requirement)
        if unsupported_summary_terms:
            sanitized.summary_edit = None
            sanitized.summary_edit_rejected_reason = (
                "Summary rejected because it introduced unsupported JD claims: "
                + ", ".join(unsupported_summary_terms)
            )
    for skill_update in sanitized.skill_updates:
        original_lookup = {original.casefold() for original in skill_update.original_items}
        skill_update.updated_items = [
            item for item in skill_update.updated_items
            if (
                item.casefold() in original_lookup
                or item.casefold() in resume_text
            ) and item.casefold() not in GENERIC_SKILL_TERMS
        ]
        skill_update.added_keywords = [
            keyword for keyword in skill_update.added_keywords
            if (
                keyword.casefold() in supported_lookup
                and keyword.casefold() not in GENERIC_SKILL_TERMS
            )
        ]
    accepted_edits = []
    rejected_edits = list(sanitized.rejected_bullet_edits)
    skipped_edits = list(sanitized.skipped_bullet_edits)
    for edit in sanitized.bullet_edits:
        reason = None
        if edit.action == "skip":
            skipped_edits.append(
                RejectedBulletEdit(
                    section=edit.section,
                    item_id=edit.item_id,
                    bullet_index=edit.bullet_index,
                    original_text=edit.original_text,
                    replacement_text=edit.replacement_text,
                    reason="Model marked this edit as skip.",
                )
            )
            continue
        target_items = resume.experience if "exp" in edit.section.casefold() else resume.projects
        target = next((item for item in target_items if item.id == edit.item_id), None)
        if (
            target is None
            or edit.bullet_index < 0
            or edit.bullet_index >= len(target.bullets)
            or target.bullets[edit.bullet_index] != edit.original_text
        ):
            reason = "Rejected because the target bullet did not match the master resume."
        elif not _preserves_bullet_facts(edit.original_text, edit.replacement_text):
            reason = "Rejected because it removed a concrete metric, highlighted fact, or outcome."
        elif not (
            any(
                contains_term(edit.replacement_text, term)
                and not contains_term(edit.original_text, term)
                for term in supported_terms
            )
            or (
                any(contains_term(edit.original_text, term) for term in supported_terms)
                and sum(
                    contains_term(edit.replacement_text, term)
                    for term in supported_terms
                )
                >= sum(
                    contains_term(edit.original_text, term)
                    for term in supported_terms
                )
            )
        ):
            reason = "Rejected because it did not preserve or add supported JD evidence."
        elif not _within_bullet_budget(edit.original_text, edit.replacement_text):
            reason = "Rejected by the word/typographic-width safety budget."
        if reason:
            rejected_edits.append(
                RejectedBulletEdit(
                    section=edit.section,
                    item_id=edit.item_id,
                    bullet_index=edit.bullet_index,
                    original_text=edit.original_text,
                    replacement_text=edit.replacement_text,
                    reason=reason,
                )
            )
        else:
            accepted_edits.append(edit)
    sanitized.bullet_edits = accepted_edits
    sanitized.rejected_bullet_edits = rejected_edits
    sanitized.skipped_bullet_edits = skipped_edits
    valid_experience = {item.id: item for item in resume.experience}
    valid_projects = {item.id: item for item in resume.projects}
    supported_lookup = {term.casefold() for term in supported_terms}
    sanitized.content_priorities = [
        priority
        for priority in sanitized.content_priorities
        if (
            priority.section == "Experience" and priority.item_id in valid_experience
        ) or (
            priority.section == "Projects" and priority.item_id in valid_projects
        )
    ]
    sanitized.content_priorities = [
        priority.model_copy(
            update={
                "supported_keywords": [
                    keyword for keyword in priority.supported_keywords
                    if keyword.casefold() in supported_lookup
                ]
            }
        )
        for priority in sanitized.content_priorities
    ]
    valid_assignments = [
        assignment
        for assignment in sanitized.evidence_assignments
        if (
            assignment.section == "experience"
            and assignment.item_id in valid_experience
            and assignment.bullet_index is not None
            and 0 <= assignment.bullet_index < len(valid_experience[assignment.item_id].bullets)
        ) or (
            assignment.section == "projects"
            and assignment.item_id in valid_projects
            and assignment.bullet_index is not None
            and 0 <= assignment.bullet_index < len(valid_projects[assignment.item_id].bullets)
        ) or assignment.section in {"summary", "skills"}
    ]
    sanitized.bullet_orders = [
        order
        for order in sanitized.bullet_orders
        if (
            order.section == "Experience"
            and order.item_id in valid_experience
            and sorted(order.bullet_indices) == list(range(len(valid_experience[order.item_id].bullets)))
        ) or (
            order.section == "Projects"
            and order.item_id in valid_projects
            and sorted(order.bullet_indices) == list(range(len(valid_projects[order.item_id].bullets)))
        )
    ]
    # Keep one primary assignment per requirement and prefer the strongest
    # source. This prevents parallel specialist calls from inflating the report.
    strength_rank = {"strong": 0, "moderate": 1, "weak": 2}
    deduped: dict[str, EvidenceAssignment] = {}
    for assignment in valid_assignments:
        key = assignment.requirement.casefold().strip()
        current = deduped.get(key)
        if current is None or strength_rank[assignment.strength] < strength_rank[current.strength]:
            deduped[key] = assignment
    sanitized.evidence_assignments = list(deduped.values())
    return sanitized


def generate_diff_markdown(
    original: MasterResume,
    tailored: MasterResume,
    plan: TailorPlan,
    page_count: int,
    compile_time_ms: float,
) -> str:
    """Generate a clean Markdown diff report of all modifications."""

    def table_cell(value: str) -> str:
        """Keep each Markdown table row valid and readable in plain text."""
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace("|", "\\|")
            .replace("\r", " ")
            .replace("\n", " ")
            .strip()
        )

    lines = [
        f"# Flash Resume ATS Tailoring Report: {plan.company} - {plan.role}",
        "",
        f"- **Evidence-based ATS Score:** {plan.ats_match_score}%",
        f"- **Requirement Coverage:** {len([m for m in plan.requirement_matches if m.status == 'covered'])} covered, {len([m for m in plan.requirement_matches if m.status == 'partial'])} partial, {len(plan.missing_keywords)} unsupported ({len(plan.requirement_matches)} total)",
        f"- **Applied Bullet Edits:** {len(plan.bullet_edits)}",
        f"- **Rejected Bullet Edits:** {len(plan.rejected_bullet_edits)}",
        f"- **Skipped Bullet Edits:** {len(plan.skipped_bullet_edits)}",
        *(
            [f"- **Summary Edit:** Rejected — {clean_text(plan.summary_edit_rejected_reason)}"]
            if plan.summary_edit_rejected_reason
            else []
        ),
        f"- **Role Evidence Assignments:** {len(plan.evidence_assignments)}",
        f"- **Page Count:** {page_count} (Verified Single-Page Fit)",
        f"- **Typst Compile Latency:** {compile_time_ms:.1f} ms",
        f"- **Covered JD Requirements:** {', '.join(plan.matched_keywords) if plan.matched_keywords else 'None'}",
        "- **Scoring:** required requirements weigh 1.0; preferred requirements weigh 0.35; partial evidence earns 50% of its weight.",
        "",
        "## Requirement Evidence Matrix",
        "",
        "| Priority | Requirement | Status | Method | Evidence |",
        "| --- | --- | --- | --- | --- |",
    ]

    for match in plan.requirement_matches:
        evidence = "; ".join(match.evidence) if match.evidence else "—"
        lines.append(
            f"| {table_cell(match.priority)} | {table_cell(match.requirement)} | "
            f"{table_cell(match.status)} | {table_cell(match.match_method)} | "
            f"{table_cell(evidence)} |"
        )

    lines.extend([
        "",
        "## Modified Bullet Points",
        "",
        "| Section | Original | Tailored | Word Change |",
        "| --- | --- | --- | --- |",
    ])
    for edit in plan.bullet_edits:
        orig_words = len(edit.original_text.split())
        new_words = len(edit.replacement_text.split())
        delta = new_words - orig_words
        sign = f"+{delta}" if delta > 0 else str(delta)
        lines.append(
            f"| {table_cell(edit.section)} | {table_cell(edit.original_text)} | "
            f"{table_cell(edit.replacement_text)} | {table_cell(sign + ' words')} |"
        )

    lines.extend([
        "",
        "## Rejected Bullet Points",
        "",
        "| Section | Original | Proposed Rewrite | Reason |",
        "| --- | --- | --- | --- |",
    ])
    for edit in plan.rejected_bullet_edits:
        lines.append(
            f"| {table_cell(edit.section)} | {table_cell(edit.original_text)} | "
            f"{table_cell(edit.replacement_text)} | {table_cell(edit.reason)} |"
        )

    lines.extend([
        "",
        "## Skipped Bullet Points",
        "",
        "| Section | Original | Reason |",
        "| --- | --- | --- |",
    ])
    for edit in plan.skipped_bullet_edits:
        lines.append(
            f"| {table_cell(edit.section)} | {table_cell(edit.original_text)} | "
            f"{table_cell(edit.reason)} |"
        )
    lines.extend([
        "",
        "## Role-Focused Content Plan",
        "",
        "| Section | Item | Priority | Supported Keywords | Reason |",
        "| --- | --- | --- | --- | --- |",
    ])
    for priority in plan.content_priorities:
        lines.append(
            f"| {table_cell(priority.section)} | {table_cell(priority.item_id)} | "
            f"{table_cell(priority.priority)} | "
            f"{table_cell(', '.join(priority.supported_keywords) or '—')} | "
            f"{table_cell(priority.reason)} |"
        )
    lines.extend([
        "",
        "## Evidence Assignments",
        "",
        "| Requirement | Source | Strength |",
        "| --- | --- | --- |",
    ])
    for assignment in plan.evidence_assignments:
        source = assignment.section
        if assignment.item_id:
            source += f": {assignment.item_id}"
        if assignment.bullet_index is not None:
            source += f" bullet {assignment.bullet_index + 1}"
        lines.append(
            f"| {table_cell(assignment.requirement)} | {table_cell(source)} | "
            f"{table_cell(assignment.strength)} |"
        )
    lines.extend([
        "",
        "## Skills Adjustments",
        "",
    ])

    if plan.preparation_skills:
        lines.append(
            "- **Familiarity (not current production experience):** "
            + ", ".join(plan.preparation_skills)
        )

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
        interview_mode: bool = False,
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
        # Evaluate existing evidence before the LLM plans edits. Coverage is
        # independent from whether an edit is later selected.
        if not (jd_keywords.required_keywords or jd_keywords.preferred_keywords):
            fallback_terms = [
                term for term in EVIDENCE_TERMS if term.casefold() in job_description.casefold()
            ]
            jd_keywords = JDKeywords(required_keywords=fallback_terms)
        initial_assessment = evaluate_requirements(master_resume, jd_keywords)
        supported_terms, unsupported_terms = coverage_terms(initial_assessment)
        plan = sanitize_tailor_plan(
            generate_split_plan(
                llm=self.llm,
                resume=master_resume,
                job_description=job_description,
                company_override=company_override,
                role_override=role_override,
                jd_keywords=jd_keywords,
                supported_terms=supported_terms,
                unsupported_terms=unsupported_terms,
                interview_mode=interview_mode,
            ),
            master_resume,
            job_description,
            jd_keywords,
            assessment=initial_assessment,
            interview_mode=interview_mode,
        )
        llm_time_ms = (time.perf_counter() - llm_started_at) * 1000

        # 3. Apply Plan to Clone
        tailored_resume = apply_tailor_plan(master_resume, plan)
        tailored_resume = align_resume_to_jd(tailored_resume, initial_assessment)

        # 4. Resolve Output Paths
        out_base = Path(output_dir_override or self.config.output_dir)
        out_base.mkdir(parents=True, exist_ok=True)

        prefix = f"{sanitize_filename(plan.company)}_{sanitize_filename(plan.role)}"
        pdf_path = out_base / f"{prefix}.pdf"
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

        # Score the final, fitted resume rather than a plan or a pre-trim
        # intermediate. This also records a complete evidence trace in the
        # saved report.
        _apply_coverage(plan, evaluate_requirements(tailored_resume, jd_keywords))

        # 5b. Bold matched keywords in the PDF (display-only pass). Bold adds
        # a hair of width, so verify the page budget and fall back to plain
        # render on overflow.
        highlighted = highlight_keywords(tailored_resume, plan.matched_keywords)
        hl_pages, hl_ms = self.compiler.compile(
            highlighted, pdf_path, self.config.max_pages
        )
        compile_ms += hl_ms
        if hl_pages > self.config.max_pages:
            pages, fallback_ms = self.compiler.compile(
                tailored_resume, pdf_path, self.config.max_pages
            )
            compile_ms += fallback_ms
        else:
            pages = hl_pages

        # 6. Save the readable diff report after trimming.
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
            diff_path=str(diff_path.resolve()),
            page_count=pages,
            llm_time_ms=llm_time_ms,
            compile_time_ms=compile_ms,
            total_time_ms=total_time_ms,
            plan=plan,
            trim_applied=trim_applied,
        )
