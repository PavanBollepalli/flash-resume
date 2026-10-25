"""Data models for ATS tailoring plans, diffs, and generation results."""

from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class BulletEdit(BaseModel):
    """A targeted edit on a single resume bullet point.

    ``original_text`` is no longer echoed back by the model: the engine resolves
    the target from ``(section, item_id, bullet_index)`` against the master
    resume and substitutes its own copy of the bullet, so a paraphrase can never
    be mistaken for a miss. ``preserved_facts`` is the model's echo of the
    target's ``keep`` list, which makes it enumerate what must survive before it
    rewrites (see ``services.edit_contract``).
    """

    action: Literal["replace", "skip"] = Field(
        default="replace",
        description="Use 'skip' when the original bullet already matches the job description.",
    )
    section: str = Field(description="Section containing the bullet, e.g., 'Experience' or 'Projects'")
    item_id: str = Field(description="Identifier of parent experience or project item")
    bullet_index: int = Field(description="0-based index of the bullet in the item's bullets list")
    original_text: str = Field(
        default="",
        description="Optional; the engine substitutes the master resume's own copy of the bullet.",
    )
    replacement_text: str = Field(description="Rewritten bullet text integrating targeted ATS keywords")
    preserved_facts: List[str] = Field(
        default_factory=list,
        description="Every entry of the target's 'keep' list, copied verbatim before rewriting",
    )


class RejectedBulletEdit(BaseModel):
    """A model-proposed bullet edit rejected by deterministic safety gates."""

    section: str
    item_id: str
    bullet_index: int
    original_text: str
    replacement_text: str
    reason: str
    code: str = Field(
        default="",
        description="Machine-readable reason key, e.g. 'facts_dropped'; used for the funnel report",
    )


class SkillUpdate(BaseModel):
    """An update to a specific skills category."""

    category: str = Field(description="Category name being updated")
    original_items: List[str] = Field(default_factory=list, description="Original skills")
    updated_items: List[str] = Field(default_factory=list, description="Updated skills list with keywords added")
    added_keywords: List[str] = Field(default_factory=list, description="Keywords added to this category")


class ContentPriority(BaseModel):
    """Role relevance ranking for an experience entry or project."""

    section: Literal["Experience", "Projects"]
    item_id: str
    priority: Literal["high", "medium", "low"]
    supported_keywords: List[str] = Field(default_factory=list)
    reason: str


class EvidenceAssignment(BaseModel):
    """Best concrete resume source for a supported JD requirement."""

    requirement: str
    section: Literal["summary", "skills", "experience", "projects"]
    item_id: Optional[str] = None
    bullet_index: Optional[int] = None
    strength: Literal["strong", "moderate", "weak"]


class BulletOrder(BaseModel):
    """Requested display order for bullets within one resume item."""

    section: Literal["Experience", "Projects"]
    item_id: str
    bullet_indices: List[int]


class CoreEditPlan(BaseModel):
    """What the core specialist is asked for: edits only.

    Bullet ordering and per-item relevance ranking used to be requested here as
    ``experience_bullet_orders``. Both are derived locally from the accepted
    edit set (``services.edit_contract``), so asking a model for them bought
    output that was then validated away, dropped, or overwritten.
    """

    company: str = "Company"
    role: str = "Software Engineer"
    summary_edit: Optional[str] = None
    skill_updates: List[SkillUpdate] = Field(default_factory=list)
    experience_bullet_edits: List[BulletEdit] = Field(default_factory=list)
    evidence_assignments: List[EvidenceAssignment] = Field(default_factory=list)
    preparation_skills: List[str] = Field(default_factory=list)


class ProjectEditPlan(BaseModel):
    """What the projects specialist is asked for: bullet edits only."""

    project_bullet_edits: List[BulletEdit] = Field(default_factory=list)
    evidence_assignments: List[EvidenceAssignment] = Field(default_factory=list)


class RequirementMatch(BaseModel):
    """Auditable evidence assessment for one JD requirement."""

    requirement: str = Field(description="Requirement as extracted from the JD")
    normalized_requirement: str = Field(description="Canonical form used for matching")
    priority: Literal["required", "preferred"] = Field(
        default="required", description="JD priority retained from extraction"
    )
    status: Literal["covered", "partial", "unsupported"] = Field(
        description="Coverage classification based on resume evidence"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: List[str] = Field(
        default_factory=list, description="Exact resume fragments supporting the classification"
    )
    evidence_locations: List[str] = Field(
        default_factory=list, description="Structured resume fields containing the evidence"
    )
    match_method: str = Field(description="exact, alias, evidence_rule, or none")
    reason: str = Field(description="Human-readable explanation of the decision")


class TailorPlan(BaseModel):
    """Structured ATS optimization plan produced by the tailoring engine."""

    company: str = Field(default="Company", description="Target company")
    role: str = Field(default="Software Engineer", description="Target role")
    ats_match_score: int = Field(default=85, description="Estimated ATS keyword coverage score (0-100)")
    matched_keywords: List[str] = Field(default_factory=list, description="Keywords already present or integrated")
    missing_keywords: List[str] = Field(default_factory=list, description="Keywords found in JD but unsupported")
    preparation_skills: List[str] = Field(
        default_factory=list,
        description="JD skills the candidate plans to learn; never treated as current experience",
    )
    soft_skills: List[str] = Field(
        default_factory=list,
        description=(
            "Generic professional skills named by the JD, carried into the resume's "
            "skills section unconditionally (never evidence-gated)"
        ),
    )
    requirement_matches: List[RequirementMatch] = Field(
        default_factory=list,
        description="Auditable requirement-to-resume evidence matrix used for ATS coverage",
    )
    skill_updates: List[SkillUpdate] = Field(default_factory=list, description="Skill section adjustments")
    bullet_edits: List[BulletEdit] = Field(default_factory=list, description="Targeted bullet point modifications")
    rejected_bullet_edits: List[RejectedBulletEdit] = Field(
        default_factory=list,
        description="Bullet edits proposed by the model but rejected with an auditable reason",
    )
    skipped_bullet_edits: List[RejectedBulletEdit] = Field(
        default_factory=list,
        description="Bullet edits the model explicitly left unchanged",
    )
    summary_edit_rejected_reason: Optional[str] = None
    content_priorities: List[ContentPriority] = Field(default_factory=list)
    evidence_assignments: List[EvidenceAssignment] = Field(default_factory=list)
    bullet_orders: List[BulletOrder] = Field(default_factory=list)
    summary_edit: Optional[str] = Field(default=None, description="Optional tailored professional bio summary")


class JDKeywords(BaseModel):
    """ATS-relevant keywords extracted from a job description by the LLM (call #1)."""

    required_keywords: List[str] = Field(
        default_factory=list,
        description="Hard requirements / ATS-critical keywords from the JD",
    )
    preferred_keywords: List[str] = Field(
        default_factory=list,
        description="Nice-to-have / preferred ('preferred qualifications') keywords",
    )


class EditFunnel(BaseModel):
    """Per-run accounting of what was proposed for bullets and what survived.

    Rejected edits are compute the run already paid for, so the shape of the
    loss belongs in the report next to the edits that landed rather than only in
    a debug log. Counts are filled by the gates themselves, so the histogram
    cannot drift from the reasons shown in the diff.
    """

    proposed: int = Field(default=0, description="Bullet edits the model returned")
    applied: int = Field(default=0, description="Bullet edits that survived every gate")
    rejected: int = Field(default=0, description="Bullet edits the gates refused")
    skipped: int = Field(default=0, description="Bullets the model explicitly left alone")
    repaired: int = Field(default=0, description="Edits accepted after a local identity/text repair")
    editable_targets: int = Field(default=0, description="Bullets that had a JD term left to add")
    locked_targets: int = Field(default=0, description="Bullets with no absorbable JD term")
    undeclared_facts: int = Field(
        default=0, description="Accepted edits whose preserved_facts echo was incomplete"
    )
    unevidenced_terms: List[str] = Field(
        default_factory=list,
        description=(
            "JD terms an accepted edit introduced that the matcher found no resume "
            "evidence for. Reported rather than rejected: the matcher has blind spots."
        ),
    )
    projects_call_skipped: bool = Field(
        default=False, description="The projects specialist call was skipped as it had no target"
    )
    legacy_fallback_used: bool = Field(
        default=False, description="Specialist calls failed and the single full-plan call ran instead"
    )
    reasons: dict[str, int] = Field(
        default_factory=dict, description="Rejection counts keyed by reason code"
    )

    def count(self, code: str) -> None:
        """Record one rejection under its machine-readable reason code."""
        self.reasons[code] = self.reasons.get(code, 0) + 1
        self.rejected += 1

    def headline(self) -> str:
        """One line describing the funnel, for the log and the diff report."""
        parts = [
            f"{self.proposed} proposed",
            f"{self.applied} applied",
            f"{self.rejected} rejected",
            f"{self.skipped} skipped",
        ]
        if self.repaired:
            parts.append(f"{self.repaired} repaired locally")
        if self.undeclared_facts:
            parts.append(f"{self.undeclared_facts} without a facts echo")
        if self.unevidenced_terms:
            parts.append(f"{len(self.unevidenced_terms)} unevidenced term(s) introduced")
        if self.projects_call_skipped:
            parts.append("projects call skipped (no target)")
        if self.legacy_fallback_used:
            parts.append("legacy single-plan fallback")
        return "Bullet edits: " + ", ".join(parts)


class TailorResult(BaseModel):
    """Final artifact generation details and verification metrics."""

    pdf_path: str = Field(description="Absolute path to generated PDF")
    diff_path: str = Field(description="Absolute path to Markdown diff report")
    page_count: int = Field(default=1, description="Verified total page count")
    llm_time_ms: float = Field(default=0.0, description="Gemini plan-generation time in ms")
    compile_time_ms: float = Field(default=0.0, description="Local Typst compilation time in ms")
    total_time_ms: float = Field(default=0.0, description="Total tailoring pipeline time in ms")
    plan: TailorPlan = Field(description="The underlying tailoring plan applied")
    edit_funnel: Optional[EditFunnel] = Field(
        default=None, description="How many proposed bullet edits landed, and why the rest did not"
    )
    trim_applied: bool = Field(
        default=False,
        description="Whether content was auto-trimmed to fit the page budget",
    )
