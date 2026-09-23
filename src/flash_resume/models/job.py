"""Data models for Job Description parsing and extraction."""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class JobAnalysis(BaseModel):
    """Extracted and analyzed requirements from a job posting."""

    company: str = Field(default="Company", description="Company or employer name inferred from JD")
    role: str = Field(default="Software Engineer", description="Job title / role inferred from JD")
    seniority: Optional[str] = Field(default=None, description="Seniority level, e.g. 'Entry-Level', 'Senior'")
    domain: Optional[str] = Field(default=None, description="Industry or engineering domain, e.g. 'FinTech', 'Cloud'")
    required_skills: List[str] = Field(default_factory=list, description="Explicit must-have technical skills")
    preferred_skills: List[str] = Field(default_factory=list, description="Nice-to-have or bonus technical skills")
    keywords: List[str] = Field(default_factory=list, description="Top high-density ATS keywords to match")
    raw_text: str = Field(default="", description="Original raw text of the job description")

