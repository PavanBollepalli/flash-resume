"""Data models for ATS tailoring plans, diffs, and generation results."""

from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class BulletEdit(BaseModel):
    """A targeted edit on a single resume bullet point."""

    action: Literal["replace", "skip"] = Field(
        default="replace",
        description="Use 'skip' when the original bullet already matches the job description.",
    )
    section: str = Field(description="Section containing the bullet, e.g., 'Experience' or 'Projects'")
    item_id: str = Field(description="Identifier of parent experience or project item")
    bullet_index: int = Field(description="0-based index of the bullet in the item's bullets list")
    original_text: str = Field(description="Existing original bullet text")
    replacement_text: str = Field(description="Rewritten bullet text integrating targeted ATS keywords")
    keywords_added: List[str] = Field(default_factory=list, description="Keywords specifically incorporated")
    word_count_delta: int = Field(default=0, description="Difference in word count (new - original)")



class RejectedBulletEdit(BaseModel):
    """A model-proposed bullet edit rejected by deterministic safety gates."""

    section: str
    item_id: str
    bullet_index: int
    original_text: str
    replacement_text: str
    reason: str


class SkillUpdate(BaseModel):
    """An update to a specific skills category."""

    category: str = Field(description="Category name being updated")
    original_items: List[str] = Field(default_factory=list, description="Original skills")
    updated_items: List[str] = Field(default_factory=list, description="Updated skills list with keywords added")
    added_keywords: List[str] = Field(default_factory=list, description="Keywords added to this category")


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


class TailorResult(BaseModel):
    """Final artifact generation details and verification metrics."""

    pdf_path: str = Field(description="Absolute path to generated PDF")
    diff_path: str = Field(description="Absolute path to Markdown diff report")
    page_count: int = Field(default=1, description="Verified total page count")
    llm_time_ms: float = Field(default=0.0, description="Gemini plan-generation time in ms")
    compile_time_ms: float = Field(default=0.0, description="Local Typst compilation time in ms")
    total_time_ms: float = Field(default=0.0, description="Total tailoring pipeline time in ms")
    plan: TailorPlan = Field(description="The underlying tailoring plan applied")
    trim_applied: bool = Field(
        default=False,
        description="Whether content was auto-trimmed to fit the page budget",
    )
