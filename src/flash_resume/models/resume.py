"""Data models for Master Resume and its structured components."""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class ContactInfo(BaseModel):
    """Personal contact information for the resume header."""

    name: str = Field(description="Full legal or professional name")
    title: Optional[str] = Field(default=None, description="Professional title, e.g., 'Full Stack Engineer'")
    email: str = Field(description="Professional email address")
    phone: str = Field(description="Phone number with country/area code")
    location: str = Field(description="City, State / Country, e.g. 'San Francisco, CA'")
    linkedin: Optional[str] = Field(default=None, description="LinkedIn profile URL or vanity handle")
    github: Optional[str] = Field(default=None, description="GitHub username or profile URL")
    portfolio: Optional[str] = Field(default=None, description="Portfolio website or blog URL")


class SkillCategory(BaseModel):
    """A grouped category of skills (e.g., Languages, Frameworks, Cloud)."""

    category: str = Field(description="Name of skill grouping, e.g., 'Languages' or 'Cloud & DevOps'")
    items: List[str] = Field(default_factory=list, description="List of specific technologies or competencies")


class ExperienceItem(BaseModel):
    """Work experience or professional employment entry."""

    id: str = Field(description="Unique identifier for this role, e.g., 'exp_google_swe'")
    company: str = Field(description="Organization or employer name")
    role: str = Field(description="Job title held")
    location: str = Field(description="Location of work, e.g. 'New York, NY (Hybrid)'")
    start_date: str = Field(description="Start date, e.g. 'June 2023'")
    end_date: str = Field(description="End date or 'Present'")
    bullets: List[str] = Field(
        default_factory=list,
        description="Accomplishment-oriented bullet points (XYZ format)",
    )


class ProjectItem(BaseModel):
    """Technical project entry."""

    id: str = Field(description="Unique identifier for this project, e.g., 'proj_distributed_cache'")
    name: str = Field(description="Project title")
    start_date: Optional[str] = Field(default=None, description="Start date, e.g. 'Jan 2025'")
    end_date: Optional[str] = Field(default=None, description="End date or 'Present'")
    technologies: List[str] = Field(default_factory=list, description="Key tech stack tools used")
    link: Optional[str] = Field(default=None, description="GitHub link or live deployment URL")
    bullets: List[str] = Field(
        default_factory=list,
        description="Project accomplishment bullets describing challenges and outcomes",
    )


class EducationItem(BaseModel):
    """Academic degree or educational credential."""

    institution: str = Field(description="University, College, or School name")
    degree: str = Field(description="Degree type, e.g., 'B.S.' or 'Master of Science'")
    field_of_study: str = Field(description="Major or field, e.g., 'Computer Science'")
    start_date: str = Field(description="Start year/month, e.g. '2020'")
    end_date: str = Field(description="Graduation year/month, e.g. '2024'")
    gpa: Optional[str] = Field(default=None, description="GPA or honors, if applicable")
    highlights: List[str] = Field(default_factory=list, description="Relevant coursework or academic honors")


class MasterResume(BaseModel):
    """Single source of truth master resume."""

    contact: ContactInfo
    summary: Optional[str] = Field(default=None, description="Short targeted 2-3 line professional bio")
    highlights: List[str] = Field(default_factory=list, description="One or two quantified differentiators")
    skills: List[SkillCategory] = Field(default_factory=list, description="Categorized technical competencies")
    experience: List[ExperienceItem] = Field(default_factory=list, description="Chronological work history")
    projects: List[ProjectItem] = Field(default_factory=list, description="Highlighted technical projects")
    education: List[EducationItem] = Field(default_factory=list, description="Educational background")
    certifications: List[str] = Field(default_factory=list, description="Certifications and licenses")

