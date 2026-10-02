"""Deterministic, auditable JD-to-resume evidence matching.

The matcher deliberately does not infer skills from unrelated text or add
claims to a resume.  It recognizes common, verifiable equivalents (for
example ``unit tests`` for ``testing``) and records every fragment it used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from flash_resume.models.resume import MasterResume
from flash_resume.models.tailoring import JDKeywords, RequirementMatch


PREFERRED_WEIGHT = 0.35
PARTIAL_CREDIT = 0.5


@dataclass(frozen=True)
class EvidenceItem:
    location: str
    text: str


@dataclass(frozen=True)
class EvidenceRule:
    aliases: tuple[str, ...]
    partial: bool = False


# These are concept aliases, not a list of skills to inject.  A rule can only
# award coverage when one of its aliases is already in a structured resume.
RULES: dict[str, EvidenceRule] = {
    "testing": EvidenceRule(("unit test", "unit tests", "test suite", "pytest", "automated test")),
    "reusable code": EvidenceRule(("reusable", "shared module", "shared utility", "library")),
    "maintainable code": EvidenceRule(("reusable", "docstring", "unit test", "code review", "modular")),
    "clean code": EvidenceRule(("reusable module", "docstring", "code review", "modular"), partial=True),
    "performance optimization": EvidenceRule(("reduced latency", "execution time", "query execution", "optimiz", "indexing", "performance")),
    "vector database": EvidenceRule(("pgvector", "pinecone", "chromadb", "vector index", "vector search", "hnsw")),
    "sql database": EvidenceRule(("sql", "postgresql", "mysql", "sqlite", "mariadb", "oracle database")),
    "api integration": EvidenceRule(("rest api", "restful api", "api endpoint", "tavily", "groq", "gemini api", "webhook")),
    "ai workflow": EvidenceRule(("rag pipeline", "rag retrieval", "llm fallback", "prompt engineering", "retrieval pipeline")),
    "cloud deployment": EvidenceRule(("aws", "google cloud", "gcp", "azure", "docker", "ci cd", "github actions", "deployment"), partial=True),
    "aws cloud": EvidenceRule(("aws", "amazon web services", "google cloud", "gcp", "azure")),
    "backend development": EvidenceRule(("fastapi", "express", "node js", "django", "flask", "backend")),
    "rest api": EvidenceRule(("rest api", "restful api", "rest api design", "api endpoint")),
    "rag pipeline": EvidenceRule(("rag pipeline", "retrieval augmented generation", "rag retrieval")),
    "intelligent agent": EvidenceRule(("agentic ai", "llm agent", "tool using", "intelligent agent")),
    "ai ml": EvidenceRule(("ai and ml", "generative ai", "machine learning", "rag pipeline")),
    "llm": EvidenceRule(("llm", "large language model", "llama")),
    "ai": EvidenceRule(("generative ai", "artificial intelligence", "rag pipeline")),
    "pytorch hugging face": EvidenceRule(("pytorch", "hugging face")),
}


def normalize(text: str) -> str:
    """Normalize case, punctuation, and simple plural forms for matching."""
    value = text.casefold().replace("&", " and ").replace("/", " ")
    value = re.sub(r"[^a-z0-9+#.]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _singular(value: str) -> str:
    words = value.split()
    if words and len(words[-1]) > 3 and words[-1].endswith("s"):
        words[-1] = words[-1][:-1]
    return " ".join(words)


def resume_evidence(resume: MasterResume) -> list[EvidenceItem]:
    """Flatten only candidate-authored resume fields into traceable evidence."""
    evidence: list[EvidenceItem] = []

    def add(location: str, text: str | None) -> None:
        if text and text.strip():
            evidence.append(EvidenceItem(location, text))

    add("summary", resume.summary)
    for index, award in enumerate(resume.awards):
        add(f"awards[{index}]", award)
    for category in resume.skills:
        for index, item in enumerate(category.items):
            add(f"skills.{category.category}[{index}]", item)
    for exp in resume.experience:
        add(f"experience.{exp.id}.role", exp.role)
        for index, bullet in enumerate(exp.bullets):
            add(f"experience.{exp.id}.bullets[{index}]", bullet)
    for project in resume.projects:
        add(f"projects.{project.id}.name", project.name)
        for index, technology in enumerate(project.technologies):
            add(f"projects.{project.id}.technologies[{index}]", technology)
        for index, bullet in enumerate(project.bullets):
            add(f"projects.{project.id}.bullets[{index}]", bullet)
    for edu in resume.education:
        add("education.field_of_study", edu.field_of_study)
    for index, certification in enumerate(resume.certifications):
        add(f"certifications[{index}]", certification)
    return evidence


def _canonical_rule(requirement: str) -> str | None:
    value = _singular(normalize(requirement))
    if "mongodb" in value and "sql database" in value:
        return "sql database"  # The JD's MongoDB / SQL-database alternative.
    if "vector" in value and "database" in value:
        return "vector database"
    if "performance" in value and ("optim" in value or "tuning" in value):
        return "performance optimization"
    if "api" in value and ("integration" in value or "integrate" in value):
        return "api integration"
    if "ai" in value and "workflow" in value:
        return "ai workflow"
    if "cloud" in value and ("deploy" in value or "infrastructure" in value):
        return "cloud deployment"
    if "aws" in value and "cloud" in value:
        return "aws cloud"
    if "maintainable" in value:
        return "maintainable code"
    if "reusable" in value:
        return "reusable code"
    if "clean" in value and "code" in value:
        return "clean code"
    if "test" in value:
        return "testing"
    if "rest" in value and "api" in value:
        return "rest api"
    if "rag" in value and ("pipeline" in value or "retrieval" in value):
        return "rag pipeline"
    if "backend" in value:
        return "backend development"
    if "intelligent agent" in value or value == "agent":
        return "intelligent agent"
    if "pytorch" in value and "hugging" in value:
        return "pytorch hugging face"
    if "ai" in value and "ml" in value:
        return "ai ml"
    if "llm" in value:
        return "llm"
    if value == "ai" or value.startswith("ai powered"):
        return "ai"
    if "sql" in value and "database" in value:
        return "sql database"
    return None


def _find_matches(aliases: Iterable[str], evidence: list[EvidenceItem]) -> list[EvidenceItem]:
    normalized_aliases = [_singular(normalize(alias)) for alias in aliases]
    found: list[EvidenceItem] = []
    for item in evidence:
        text = _singular(normalize(item.text))
        if any(alias and alias in text for alias in normalized_aliases):
            found.append(item)
    return found


def _evaluate_one(requirement: str, priority: str, evidence: list[EvidenceItem]) -> RequirementMatch:
    normalized = _singular(normalize(requirement))
    exact = _find_matches((requirement,), evidence)
    if exact:
        return RequirementMatch(
            requirement=requirement, normalized_requirement=normalized, priority=priority,
            status="covered", confidence=1.0, evidence=[item.text for item in exact[:3]],
            evidence_locations=[item.location for item in exact[:3]], match_method="exact",
            reason="The requirement appears directly in the candidate-authored resume.",
        )

    rule_name = _canonical_rule(requirement)
    rule = RULES.get(rule_name or "")
    aliases = rule.aliases if rule else ()
    matched = _find_matches(aliases, evidence)
    if matched:
        status = "partial" if rule and rule.partial else "covered"
        confidence = 0.65 if status == "partial" else 0.9
        return RequirementMatch(
            requirement=requirement, normalized_requirement=rule_name or normalized, priority=priority,
            status=status, confidence=confidence, evidence=[item.text for item in matched[:3]],
            evidence_locations=[item.location for item in matched[:3]], match_method="evidence_rule",
            reason=("Related, verifiable resume evidence supports part of this compound requirement."
                    if status == "partial" else "A verified equivalent is present in the resume."),
        )

    return RequirementMatch(
        requirement=requirement, normalized_requirement=rule_name or normalized, priority=priority,
        status="unsupported", confidence=0.0, evidence=[], evidence_locations=[], match_method="none",
        reason="No direct or approved-equivalent evidence was found in the resume.",
    )


def evaluate_requirements(resume: MasterResume, jd_keywords: JDKeywords | None) -> list[RequirementMatch]:
    """Evaluate every extracted JD requirement against the original resume."""
    if not jd_keywords:
        return []
    candidates = [(term, "required") for term in jd_keywords.required_keywords]
    candidates += [(term, "preferred") for term in jd_keywords.preferred_keywords]
    seen: set[tuple[str, str]] = set()
    assessment: list[RequirementMatch] = []
    evidence = resume_evidence(resume)
    for term, priority in candidates:
        term = term.strip()
        key = (normalize(term), priority)
        if term and key not in seen:
            seen.add(key)
            assessment.append(_evaluate_one(term, priority, evidence))
    return assessment


def coverage_score(assessment: list[RequirementMatch]) -> int:
    """Required items are weighted 1.0; preferred items 0.35; partial evidence earns 50%."""
    total = sum(1.0 if item.priority == "required" else PREFERRED_WEIGHT for item in assessment)
    earned = sum(
        (1.0 if item.priority == "required" else PREFERRED_WEIGHT)
        * (1.0 if item.status == "covered" else PARTIAL_CREDIT if item.status == "partial" else 0.0)
        for item in assessment
    )
    return round(earned / total * 100) if total else 0


def coverage_terms(assessment: list[RequirementMatch]) -> tuple[list[str], list[str]]:
    """Return covered/partial and unsupported requirements for existing UI compatibility."""
    supported = [item.requirement for item in assessment if item.status != "unsupported"]
    unsupported = [item.requirement for item in assessment if item.status == "unsupported"]
    return supported, unsupported


def matched_evidence_terms(assessment: list[RequirementMatch]) -> list[str]:
    """Return exact evidence phrases suitable for optional PDF highlighting."""
    return list(dict.fromkeys(fragment for item in assessment for fragment in item.evidence))
