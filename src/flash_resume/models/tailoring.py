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


class SkillUpdate(BaseModel):
    """An update to a specific skills category."""

    category: str = Field(description="Category name being updated")
    original_items: List[str] = Field(default_factory=list, description="Original skills")
    updated_items: List[str] = Field(default_factory=list, description="Updated skills list with keywords added")
    added_keywords: List[str] = Field(default_factory=list, description="Keywords added to this category")


class TailorPlan(BaseModel):
    """Structured ATS optimization plan produced by the tailoring engine."""

    company: str = Field(default="Company", description="Target company")
    role: str = Field(default="Software Engineer", description="Target role")
    ats_match_score: int = Field(default=85, description="Estimated ATS keyword coverage score (0-100)")
    matched_keywords: List[str] = Field(default_factory=list, description="Keywords already present or integrated")
    missing_keywords: List[str] = Field(default_factory=list, description="Keywords found in JD but unsupported")
    skill_updates: List[SkillUpdate] = Field(default_factory=list, description="Skill section adjustments")
    bullet_edits: List[BulletEdit] = Field(default_factory=list, description="Targeted bullet point modifications")
    summary_edit: Optional[str] = Field(default=None, description="Optional tailored professional bio summary")


class TailorResult(BaseModel):
    """Final artifact generation details and verification metrics."""

    pdf_path: str = Field(description="Absolute path to generated PDF")
    json_path: str = Field(description="Absolute path to tailored resume JSON")
    diff_path: str = Field(description="Absolute path to Markdown diff report")
    page_count: int = Field(default=1, description="Verified total page count")
    llm_time_ms: float = Field(default=0.0, description="Gemini plan-generation time in ms")
    compile_time_ms: float = Field(default=0.0, description="Local Typst compilation time in ms")
    total_time_ms: float = Field(default=0.0, description="Total tailoring pipeline time in ms")
    plan: TailorPlan = Field(description="The underlying tailoring plan applied")

