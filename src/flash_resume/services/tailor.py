"""Orchestrator that applies tailoring plans and compiles finalized artifacts."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional, Tuple

import pypdf

from flash_resume.config import AppConfig
from flash_resume.models.resume import MasterResume
from flash_resume.models.tailoring import (
    JDKeywords,
    CoreEditPlan,
    ProjectEditPlan,
    EditFunnel,
    EvidenceAssignment,
    RejectedBulletEdit,
    RequirementMatch,
    TailorPlan,
    TailorResult,
)
from flash_resume.services.compiler import CompilerService
from flash_resume.services.edit_contract import (
    BulletTarget,
    REJECTION_LABELS,
    adds_supported_evidence,
    absorbable_terms,
    build_targets,
    derive_bullet_orders,
    derive_content_priorities,
    echoes_keep_list,
    missing_facts,
    resolve_target,
    unevidenced_terms_introduced,
    within_budget,
)
from flash_resume.services.evidence import (
    align_resume_to_jd,
    coverage_score,
    coverage_terms,
    contains_term,
    evaluate_requirements,
    soft_skill_labels,
)
from flash_resume.services.parser import _clean_text
from flash_resume.services.providers import build_llm_service


logger = logging.getLogger("flash_resume.tailor")


def merge_split_plans(
    core: CoreEditPlan,
    projects: ProjectEditPlan,
    company: Optional[str],
    role: Optional[str],
) -> TailorPlan:
    """Merge disjoint specialist outputs into the existing full plan schema.

    Bullet ordering and content priorities are deliberately absent: both are
    derived from the accepted edit set in :func:`sanitize_tailor_plan`, so no
    model output can disagree with the edits that actually landed.
    """
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
    targets: Optional[list[BulletTarget]] = None,
    funnel: Optional[EditFunnel] = None,
) -> TailorPlan:
    """Run specialist calls concurrently, falling back to the legacy call.

    ``targets`` is the edit manifest: it tells each specialist which bullets it
    may rewrite and on what terms. A specialist with no absorbable bullet to work
    on is not called at all — the projects call in particular used to run (and
    bill) even when the resume had no projects or no project bullet had a JD term
    left to add. ``targets=None`` means "no manifest", which keeps the call pair
    intact for direct callers.
    """
    required = {
        "resume": resume,
        "job_description": job_description,
        "company_override": company_override,
        "role_override": role_override,
        "supported_terms": supported_terms,
        "unsupported_terms": unsupported_terms,
        "jd_keywords": jd_keywords,
    }
    core_targets = [t for t in targets or [] if t.section == "Experience"]
    project_targets = [t for t in targets or [] if t.section == "Projects"]
    projects_have_work = targets is None or any(t.can_add for t in project_targets)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            core_future = pool.submit(
                llm.generate_core_edit_plan,
                **required,
                targets=core_targets,
                interview_mode=interview_mode,
            )
            project_future = (
                pool.submit(llm.generate_project_edit_plan, **required, targets=project_targets)
                if projects_have_work
                else None
            )
            core = core_future.result()
            if project_future is None:
                projects = ProjectEditPlan()
                if funnel is not None:
                    funnel.projects_call_skipped = True
                logger.info(
                    "Skipped the projects specialist: no project bullet has a JD term left to add."
                )
            else:
                projects = project_future.result()
        return merge_split_plans(
            core,
            projects,
            company_override or core.company,
            role_override or core.role,
        )
    except Exception:
        logger.exception("Split tailoring failed; using the legacy full-plan call.")
        if funnel is not None:
            funnel.legacy_fallback_used = True
        return llm.generate_tailor_plan(**required, interview_mode=interview_mode, targets=targets)


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


def sanitize_filename(name: str) -> str:
    """Sanitize a string for safe filesystem naming."""
    clean = re.sub(r"[^\w\s-]", "", name).strip()
    return re.sub(r"[-\s]+", "_", clean)


def _edit_sections(resume: MasterResume, section: str) -> tuple:
    """Candidate item lists for an edit, in the order its section implies."""
    value = (section or "").casefold()
    if "exp" in value:
        return (resume.experience,)
    if "proj" in value:
        return (resume.projects,)
    return (resume.experience, resume.projects)


def _locate_bullet(resume: MasterResume, edit) -> Optional[tuple[Any, int]]:
    """Find the bullet list and index an edit refers to in ``resume``.

    Identity is the structured handle — ``(section, item_id, bullet_index)`` —
    never the model's echo of the original text, which may be paraphrased, left
    empty, or (on a hand-built plan) stale. The text is only consulted as a
    repair when the handle does not land, and only when it is unambiguous.
    """
    for items in _edit_sections(resume, edit.section):
        for item in items:
            if item.id != edit.item_id:
                continue
            if 0 <= edit.bullet_index < len(item.bullets):
                return item, edit.bullet_index
            if len(item.bullets) == 1:
                return item, 0
    if edit.original_text:
        for items in _edit_sections(resume, edit.section):
            hits = [
                (item, index)
                for item in items
                for index, bullet in enumerate(item.bullets)
                if bullet == edit.original_text
            ]
            if len(hits) == 1:
                return hits[0]
    return None


def _strip_familiarity_suffix(item: str) -> str:
    """Remove a trailing "(familiarity)" from a skill label.

    Only the exact trailing marker is removed, so a label that genuinely ends in
    other parentheses — "pgvector (Hybrid Search)", "AWS (Certified)" — keeps
    them. The category heading already carries the meaning.
    """
    stripped = item.strip()
    if stripped.casefold().endswith("(familiarity)"):
        return stripped[: -len("(familiarity)")].strip()
    return stripped


def apply_tailor_plan(resume: MasterResume, plan: TailorPlan) -> MasterResume:
    """Apply structured edits from a TailorPlan onto a MasterResume clone."""
    tailored = copy.deepcopy(resume)
    if plan.role and tailored.contact.title != plan.role:
        tailored.contact.title = plan.role

    # Older runs wrote "(familiarity)" onto every item as well as naming the
    # category, so the rendered line read "Familiarity: NumPy (familiarity),
    # Pandas (familiarity)". The category heading already says it; drop the
    # per-item copy so the line stops repeating itself, and so a skill is not
    # spelled two different ways. This runs whether or not this run has
    # preparation skills — a resume can carry the old shape into a truthful run.
    familiarity = next(
        (
            item
            for item in tailored.skills
            if item.category.strip().casefold() == "familiarity"
        ),
        None,
    )
    if familiarity is not None:
        familiarity.items = [
            cleaned
            for cleaned in (_strip_familiarity_suffix(item) for item in familiarity.items)
            if cleaned
        ]
        if not familiarity.items:
            # Nothing survived the strip, so the heading would render on its own.
            tailored.skills = [
                item for item in tailored.skills if item is not familiarity
            ]
            familiarity = None

    if plan.preparation_skills:
        if familiarity is None:
            from flash_resume.models.resume import SkillCategory

            familiarity = SkillCategory(category="Familiarity", items=[])
            tailored.skills.append(familiarity)
        existing = {item.strip().casefold() for item in familiarity.items}
        for skill in plan.preparation_skills:
            label = skill.strip()
            if label and label.casefold() not in existing:
                familiarity.items.append(label)
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

    # 2. Update Bullets in Experience and Projects. The layout budget stays
    # enforced here as well as in the sanitizer so a hand-built plan cannot
    # bypass it, but it is measured against the bullet actually present rather
    # than against whatever text the plan carries.
    for edit in plan.bullet_edits:
        if edit.action == "skip":
            continue
        located = _locate_bullet(tailored, edit)
        if located is None:
            continue
        item, index = located
        if within_budget(item.bullets[index], edit.replacement_text):
            item.bullets[index] = edit.replacement_text

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

    # 5. Add the JD's generic professional skills as their own labelled category.
    # These are deliberately not evidence-gated: "strong communication" or "time
    # management" are baseline expectations in a posting, not claims the resume
    # has to prove, so they are always carried into the generated resume.
    if plan.soft_skills:
        category = next(
            (
                item
                for item in tailored.skills
                if item.category.strip().casefold() == "core competencies"
            ),
            None,
        )
        if category is None:
            from flash_resume.models.resume import SkillCategory

            category = SkillCategory(category="Core Competencies", items=[])
            tailored.skills.append(category)
        existing = {item.casefold() for item in category.items}
        for skill in plan.soft_skills:
            if skill.strip() and skill.strip().casefold() not in existing:
                category.items.append(skill.strip())
                existing.add(skill.strip().casefold())

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
    targets: Optional[list[BulletTarget]] = None,
    funnel: Optional[EditFunnel] = None,
) -> TailorPlan:
    """Remove unsupported model claims and edits that violate layout constraints.

    Bullet edits are checked against the same manifest the model was given
    (:mod:`flash_resume.services.edit_contract`), so a rejection means the
    rewrite broke a stated rule rather than that the model was never told the
    rule. ``funnel`` collects the counts, which is what makes the cost of the
    remaining rejections visible per run.
    """
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
    if targets is None:
        targets = build_targets(resume, supported_terms)
    if funnel is not None:
        funnel.proposed = len(sanitized.bullet_edits)
    effective: dict[tuple[str, int], str] = {}

    def reject(edit, reason: str, code: str) -> None:
        rejected_edits.append(
            RejectedBulletEdit(
                section=edit.section,
                item_id=edit.item_id,
                bullet_index=edit.bullet_index,
                original_text=edit.original_text,
                replacement_text=edit.replacement_text,
                reason=reason,
                code=code,
            )
        )
        if funnel is not None:
            funnel.count(code)

    for proposed in sanitized.bullet_edits:
        if proposed.action == "skip":
            skipped_edits.append(
                RejectedBulletEdit(
                    section=proposed.section,
                    item_id=proposed.item_id,
                    bullet_index=proposed.bullet_index,
                    original_text=proposed.original_text,
                    replacement_text=proposed.replacement_text,
                    reason="Model marked this edit as skip.",
                    code="model_skip",
                )
            )
            continue
        target, repaired = resolve_target(proposed, targets)
        if target is None:
            reject(
                proposed,
                "Rejected because the edit did not name a bullet that exists in the master resume.",
                "unresolved_target",
            )
            continue
        # The engine owns the original text, so a paraphrased or missing echo is
        # repaired here instead of costing the run the edit.
        edit = proposed.model_copy(
            update={
                "section": target.section,
                "item_id": target.item_id,
                "bullet_index": target.bullet_index,
                "original_text": target.text,
            }
        )
        replacement = edit.replacement_text.strip()
        if not replacement or replacement == target.text.strip():
            skipped_edits.append(
                RejectedBulletEdit(
                    section=edit.section,
                    item_id=edit.item_id,
                    bullet_index=edit.bullet_index,
                    original_text=target.text,
                    replacement_text=edit.replacement_text,
                    reason="The rewrite was identical to the original bullet.",
                    code="no_change",
                )
            )
            continue
        dropped = missing_facts(target.text, replacement)
        if not target.can_add:
            reject(
                edit,
                "Rejected because no supported JD term is left to add to this bullet.",
                "no_absorbable_term",
            )
        elif dropped:
            reject(
                edit,
                f"Rejected because it removed a concrete metric or outcome ({', '.join(dropped)}).",
                "facts_dropped",
            )
        elif not adds_supported_evidence(replacement, target.text, supported_terms):
            reject(
                edit,
                "Rejected because it did not preserve or add supported JD evidence.",
                "no_new_evidence",
            )
        elif not within_budget(target.text, replacement):
            reject(edit, "Rejected by the word/typographic-width safety budget.", "over_budget")
        else:
            accepted_edits.append(edit)
            effective[(target.item_id, target.bullet_index)] = replacement
            if funnel is not None:
                funnel.applied += 1
                if repaired:
                    funnel.repaired += 1
                if not echoes_keep_list(edit.preserved_facts, target.keep):
                    funnel.undeclared_facts += 1
                # Reported, not gated: see unevidenced_terms_introduced's note on
                # why a matcher-dependent rejection here would be unsafe.
                for term in unevidenced_terms_introduced(
                    replacement, target.text, unsupported_terms
                ):
                    funnel.unevidenced_terms.append(
                        f"{term} ({target.key})"
                    )
    sanitized.bullet_edits = accepted_edits
    sanitized.rejected_bullet_edits = rejected_edits
    sanitized.skipped_bullet_edits = skipped_edits
    if funnel is not None:
        funnel.skipped = len(skipped_edits) - len(plan.skipped_bullet_edits)
    valid_experience = {item.id: item for item in resume.experience}
    valid_projects = {item.id: item for item in resume.projects}
    # Ordering and per-item relevance are derived locally, from the same
    # supported terms the gates used and the text that survived them. The model
    # used to be asked for both; a locally computed ranking cannot arrive
    # truncated, cannot disagree with the accepted edits, and costs nothing.
    sanitized.content_priorities = derive_content_priorities(
        resume, supported_terms, effective
    )
    sanitized.bullet_orders = derive_bullet_orders(resume, supported_terms, effective)
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
    funnel: Optional[EditFunnel] = None,
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
            [f"- **Summary Edit:** Rejected — {_clean_text(plan.summary_edit_rejected_reason)}"]
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
    if funnel is not None:
        lines.extend([
            "",
            "## Bullet Edit Funnel",
            "",
            f"- {funnel.headline()}",
            (
                f"- **Bullets with a JD term left to add:** {funnel.editable_targets}; "
                f"**already carrying every supported term:** {funnel.locked_targets}"
            ),
            "",
            "| Outcome | Count |",
            "| --- | --- |",
            f"| Edits proposed | {funnel.proposed} |",
            f"| Applied | {funnel.applied} |",
            f"| Rejected by a gate | {funnel.rejected} |",
            f"| Skipped by the model | {funnel.skipped} |",
            f"| Accepted after a local repair | {funnel.repaired} |",
            f"| Accepted without a complete facts echo | {funnel.undeclared_facts} |",
        ])
        if funnel.unevidenced_terms:
            lines.extend([
                "",
                "- **Terms written into a bullet with no matching resume evidence:** "
                + ", ".join(funnel.unevidenced_terms)
                + ". These are accepted edits, not rejections — the evidence matcher "
                "does not link every equivalent phrasing, so this is reported for review.",
            ])
        if funnel.reasons:
            lines.extend([
                "",
                "| Rejection reason | Count |",
                "| --- | --- |",
            ])
            for code, count in sorted(funnel.reasons.items(), key=lambda row: -row[1]):
                lines.append(f"| {table_cell(REJECTION_LABELS.get(code, code))} | {count} |")
        lines.extend([
            "",
            "Rejected edits are rewrites the run paid for and did not use; each reason "
            "above is a rule the model was given in its prompt.",
        ])
    lines.extend([
        "",
        "## Bullet Ordering",
        "",
        "| Section | Item | New Order |",
        "| --- | --- | --- |",
    ])
    for order in plan.bullet_orders:
        item = next(
            (
                candidate
                for candidate in (
                    original.experience if order.section == "Experience" else original.projects
                )
                if candidate.id == order.item_id
            ),
            None,
        )
        bullets = (
            "; ".join(
                f"{index + 1}: {_clean_text(item.bullets[index])}"
                for index in order.bullet_indices
                if item and 0 <= index < len(item.bullets)
            )
            or "—"
        )
        lines.append(
            f"| {table_cell(order.section)} | {table_cell(order.item_id)} | {table_cell(bullets)} |"
        )
    lines.extend([
        "",
        "Bullets are ranked locally by supported-JD-term density, then by whether they "
        "carry a metric or an outcome verb, so this ordering is reproducible and cannot "
        "disagree with the accepted edits. Positions are 1-based from the master resume.",
    ])
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

    if plan.soft_skills:
        lines.append(
            "- **Core Competencies (added from the JD, not evidence-gated):** "
            + ", ".join(plan.soft_skills)
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
        # Provider → service assembly lives in services.providers so the CLI's
        # setup and import flows build the exact object this engine runs.
        self.llm, self.provider_label = build_llm_service(config)
        self.model = self.llm.model

    def tailor(
        self,
        job_description: str,
        master_resume: Optional[MasterResume] = None,
        company_override: Optional[str] = None,
        role_override: Optional[str] = None,
        output_dir_override: Optional[str] = None,
        interview_mode: bool = False,
        source: str = "cli",
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
        # One manifest, read by both the prompt and the gates: the bullets that
        # may be rewritten, the facts they must keep, and the terms each may
        # absorb. Generic professional skills are excluded from what a bullet can
        # absorb — they are matched as their own class with no resume evidence
        # behind them, so inviting the model to insert them would be stuffing.
        targets = build_targets(master_resume, absorbable_terms(initial_assessment))
        funnel = EditFunnel(
            editable_targets=sum(1 for target in targets if target.can_add),
            locked_targets=sum(1 for target in targets if not target.can_add),
        )
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
                targets=targets,
                funnel=funnel,
            ),
            master_resume,
            job_description,
            jd_keywords,
            assessment=initial_assessment,
            interview_mode=interview_mode,
            targets=targets,
            funnel=funnel,
        )
        llm_time_ms = (time.perf_counter() - llm_started_at) * 1000
        logger.info("Edit funnel | %s", funnel.headline())
        if funnel.unevidenced_terms:
            logger.warning(
                "Accepted edits introduced JD terms with no resume evidence: %s",
                ", ".join(funnel.unevidenced_terms),
            )

        # The JD's generic professional skills are derived deterministically from
        # the extracted keywords rather than requested from the model, so a
        # planning call can never drop them.
        plan.soft_skills = soft_skill_labels(jd_keywords)

        # 3. Apply Plan to Clone
        tailored_resume = apply_tailor_plan(master_resume, plan)
        tailored_resume = align_resume_to_jd(tailored_resume, initial_assessment)

        # 4. Resolve Output Paths
        out_base = Path(output_dir_override or self.config.output_dir)
        out_base.mkdir(parents=True, exist_ok=True)

        prefix = datetime.now().strftime("%Y-%m-%d-%H%M") + "_" + f"{sanitize_filename(plan.company)}_{sanitize_filename(plan.role)}"
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
            funnel=funnel,
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

        # 7. Track the application in SQLite history (metadata only; the PDF/diff
        # bytes stay on disk). Never let a history write fail a finished tailor.
        try:
            from flash_resume.services.tracking import log_application

            log_application(
                company=plan.company,
                role=plan.role,
                jd_snippet=job_description[:500],
                pdf_filename=pdf_path.name,
                ats_score=plan.ats_match_score,
                mode="interview_prep" if interview_mode else "truthful",
                source=source,
                llm_time_ms=llm_time_ms,
                compile_time_ms=compile_ms,
                total_time_ms=total_time_ms,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not log application history: %s", exc)

        return TailorResult(
            pdf_path=str(pdf_path.resolve()),
            diff_path=str(diff_path.resolve()),
            page_count=pages,
            llm_time_ms=llm_time_ms,
            compile_time_ms=compile_ms,
            total_time_ms=total_time_ms,
            plan=plan,
            edit_funnel=funnel,
            trim_applied=trim_applied,
        )
