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

# Only interchangeable surface forms belong here. These pairs let the
# rendered resume mirror the JD without treating a broader capability as proof
# of a narrower one.
SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("Postgres", "PostgreSQL"),
    ("REST API", "REST APIs", "RESTful API", "RESTful APIs"),
    ("Microsoft Office", "MS Office"),
    ("AWS", "Amazon Web Services"),
    ("GCP", "Google Cloud", "Google Cloud Platform"),
    ("Kubernetes", "K8s"),
    ("Node.js", "Node JS", "NodeJS"),
    ("JavaScript", "JS"),
    ("TypeScript", "TS"),
)

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
    "ai model development": EvidenceRule(
        ("machine learning", "deep learning", "generative ai", "rag system", "rag pipeline", "model development"),
        partial=True,
    ),
    "machine learning algorithms": EvidenceRule(
        ("machine learning", "deep learning", "algorithm", "model", "neural network"),
        partial=True,
    ),
    "natural language processing": EvidenceRule(
        ("natural language processing", "nlp", "text extraction", "language model"),
        partial=True,
    ),
    "computer vision": EvidenceRule(
        ("computer vision", "opencv", "ocr", "image processing", "vision model"),
        partial=True,
    ),
    "data analysis": EvidenceRule(
        ("data analysis", "analytics", "analytics team", "dashboard", "data processing", "sql query"),
        partial=True,
    ),
    "algorithm optimization": EvidenceRule(
        ("algorithm optimization", "optimized", "optimization", "reduced latency", "query execution", "indexing"),
        partial=True,
    ),
    "poc development": EvidenceRule(
        ("proof of concept", "prototype", "prototyping", "built a", "developed a"),
        partial=True,
    ),
    "bachelors degree": EvidenceRule(("bachelor", "b.tech", "b tech", "bachelor of technology")),
    "cloud platform": EvidenceRule(("aws", "amazon web services", "gcp", "google cloud", "azure"), partial=True),
    "ethical ai privacy": EvidenceRule(("ethical ai", "privacy", "privacy-first", "data privacy", "secure", "security")),
    "security experience": EvidenceRule(("security", "secure", "authentication", "privacy", "middleware")),
    "chatbot": EvidenceRule(
        ("chatbot", "chatbots", "virtual assistant", "conversational ai", "chat assistant")
    ),
    "testing": EvidenceRule(("unit test", "unit tests", "test suite", "pytest", "automated test")),
    "reusable code": EvidenceRule(("reusable", "shared module", "shared utility", "library")),
    "maintainable code": EvidenceRule(("reusable", "docstring", "unit test", "code review", "modular")),
    "clean code": EvidenceRule(("reusable module", "docstring", "code review", "modular"), partial=True),
    "performance optimization": EvidenceRule(("reduced latency", "execution time", "query execution", "optimiz", "indexing", "performance")),
    "vector database": EvidenceRule(("pgvector", "pinecone", "chromadb", "vector index", "vector search", "hnsw")),
    "sql database": EvidenceRule(("sql", "postgresql", "mysql", "sqlite", "mariadb", "oracle database")),
    "api integration": EvidenceRule(("rest api", "restful api", "api endpoint", "tavily", "groq", "gemini api", "webhook")),
    "api product development": EvidenceRule(("api product", "api development", "rest api", "restful api", "api endpoint")),
    "data structures": EvidenceRule(("data structure", "algorithms", "algorithmic problem solving")),
    "software design": EvidenceRule(("software design", "system design", "architecture", "modular")),
    "design principles": EvidenceRule(
        ("design principle", "software design", "system design", "architecture", "modular"),
        partial=True,
    ),
    "object oriented programming": EvidenceRule(
        ("object oriented", "object-oriented", "oop", "classes", "inheritance", "polymorphism")
    ),
    "concurrency": EvidenceRule(
        ("concurrency", "concurrent", "parallel", "parallel process", "asynchronous", "async")
    ),
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


_SYNONYM_LOOKUP: dict[str, tuple[str, ...]] = {}
for _group in SYNONYM_GROUPS:
    for _term in _group:
        _SYNONYM_LOOKUP[normalize(_term)] = _group


def synonym_aliases(term: str) -> tuple[str, ...]:
    """Return curated equivalent surface forms for a JD term."""
    return _SYNONYM_LOOKUP.get(normalize(term), ())


def contains_term(text: str, term: str) -> bool:
    """Match a term as text, avoiding partial matches inside longer words."""
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.IGNORECASE) is not None


def _replace_term(text: str, source: str, target: str) -> str:
    """Replace one curated synonym while preserving all surrounding text."""
    return re.sub(
        r"(?<!\w)" + re.escape(source) + r"(?!\w)",
        target,
        text,
        flags=re.IGNORECASE,
    )


def align_text_to_jd(text: str, jd_term: str) -> str:
    """Use a JD's exact synonym wording without flattening extra detail."""
    if not text or not jd_term or contains_term(text, jd_term):
        return text
    aliases = synonym_aliases(jd_term)
    for alias in sorted(aliases, key=len, reverse=True):
        if alias.casefold() != jd_term.casefold() and contains_term(text, alias):
            return _replace_term(text, alias, jd_term)
    return text


def align_resume_to_jd(resume: MasterResume, assessments: list[RequirementMatch]) -> MasterResume:
    """Align curated synonyms in skills and bullets to supported JD wording."""
    aligned = resume.model_copy(deep=True)
    terms = [
        match.requirement
        for match in assessments
        if match.status in {"covered", "partial"} and synonym_aliases(match.requirement)
    ]

    for category in aligned.skills:
        category.items = [
            _align_text_terms(item, terms)
            for item in category.items
        ]
    for experience in aligned.experience:
        experience.bullets = [_align_text_terms(bullet, terms) for bullet in experience.bullets]
    for project in aligned.projects:
        project.bullets = [_align_text_terms(bullet, terms) for bullet in project.bullets]
    return aligned


def _align_text_terms(text: str, terms: list[str]) -> str:
    for term in terms:
        text = align_text_to_jd(text, term)
    return text


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
        add("education.degree", edu.degree)
        add("education.field_of_study", edu.field_of_study)
    for index, certification in enumerate(resume.certifications):
        add(f"certifications[{index}]", certification)
    return evidence


def _canonical_rule(requirement: str) -> str | None:
    value = _singular(normalize(requirement))
    if "ai model" in value or "model development" in value:
        return "ai model development"
    if "chatbot" in value or "virtual assistant" in value:
        return "chatbot"
    if "machine learning algorithm" in value:
        return "machine learning algorithms"
    if "natural language" in value or value == "nlp technique":
        return "natural language processing"
    if "computer vision" in value:
        return "computer vision"
    if "data analys" in value:
        return "data analysis"
    if "algorithm optimization" in value:
        return "algorithm optimization"
    if "proof of concept" in value or value.startswith("poc"):
        return "poc development"
    if "bachelor" in value and len(value.split()) <= 4:
        return "bachelors degree"
    if value in {"aws", "gcp", "google cloud", "azure"}:
        return "cloud platform"
    if "ethical ai" in value or "privacy regulation" in value:
        return "ethical ai privacy"
    if value == "security experience" or value.startswith("security"):
        return "security experience"
    if "mongodb" in value and "sql database" in value:
        return "sql database"  # The JD's MongoDB / SQL-database alternative.
    if "vector" in value and "database" in value:
        return "vector database"
    if "performance" in value and ("optim" in value or "tuning" in value):
        return "performance optimization"
    if "performance" in value:
        return "performance optimization"
    if "api" in value and ("integration" in value or "integrate" in value):
        return "api integration"
    if "api" in value and ("product" in value or "develop" in value):
        return "api product development"
    if "data structure" in value:
        return "data structures"
    if "software design" in value or "system design" in value:
        return "software design"
    if "design principle" in value:
        return "design principles"
    if "object oriented" in value:
        return "object oriented programming"
    if "concurr" in value or "parallel" in value or "asynchronous" in value:
        return "concurrency"
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


def _compound_parts(requirement: str) -> tuple[str, list[str]] | None:
    """Return a compound requirement's operator and atomic alternatives."""
    if "/" in requirement:
        parts = [part.strip() for part in requirement.split("/") if part.strip()]
        if len(parts) > 1:
            return "or", parts
    if "," in requirement:
        parts = [part.strip() for part in requirement.split(",") if part.strip()]
        if 1 < len(parts) <= 5:
            return "or", parts
    parts = re.split(r"\s+(?:or|either)\s+", requirement, flags=re.IGNORECASE)
    if len(parts) > 1 and not any(
        marker in requirement.casefold() for marker in ("or equivalent", "or similar")
    ):
        return "or", [part.strip() for part in parts if part.strip()]
    parts = re.split(r"\s+and\s+", requirement, flags=re.IGNORECASE)
    if len(parts) > 1:
        return "and", [part.strip() for part in parts if part.strip()]
    return None


def _is_context_requirement(requirement: str) -> bool:
    """Exclude job-title/context phrases from technical ATS scoring."""
    value = _singular(normalize(requirement))
    return bool(
        re.search(r"\b(engineer|developer|designer|analyst|manager)\b", value)
        and len(value.split()) <= 5
    )


def _find_matches(aliases: Iterable[str], evidence: list[EvidenceItem]) -> list[EvidenceItem]:
    normalized_aliases = [_singular(normalize(alias)) for alias in aliases]
    found: list[EvidenceItem] = []
    for item in evidence:
        text = _singular(normalize(item.text))
        if any(
            alias
            and (
                contains_term(text, alias)
                or contains_term(text, alias + "s")
            )
            for alias in normalized_aliases
        ):
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

    aliases = synonym_aliases(requirement)
    synonym_matches = _find_matches(aliases, evidence) if aliases else []
    if synonym_matches:
        return RequirementMatch(
            requirement=requirement, normalized_requirement=normalized, priority=priority,
            status="covered", confidence=1.0, evidence=[item.text for item in synonym_matches[:3]],
            evidence_locations=[item.location for item in synonym_matches[:3]], match_method="synonym",
            reason="An equivalent curated technology name appears in the candidate-authored resume.",
        )

    compound = _compound_parts(requirement)
    if compound:
        operator, parts = compound
        part_results = [
            (part, _evaluate_one(part, priority, evidence))
            for part in parts
        ]
        matched_parts = [
            (part, result)
            for part, result in part_results
            if result.status != "unsupported"
        ]
        if matched_parts:
            matched_evidence = [
                (text, location)
                for _, result in matched_parts
                for text, location in zip(
                    result.evidence[:2], result.evidence_locations[:2]
                )
            ]
            if operator == "or":
                status = "covered" if any(
                    result.status == "covered" for _, result in matched_parts
                ) else "partial"
                reason = "At least one accepted alternative is present in the candidate-authored resume."
            else:
                status = (
                    "covered"
                    if len(matched_parts) == len(parts)
                    and all(result.status == "covered" for _, result in matched_parts)
                    else "partial"
                )
                reason = (
                    "All parts of the compound requirement are supported."
                    if status == "covered"
                    else "Some, but not all, parts of the compound requirement are supported."
                )
            return RequirementMatch(
                requirement=requirement,
                normalized_requirement=normalized,
                priority=priority,
                status=status,
                confidence=1.0 if status == "covered" else 0.65,
                evidence=[text for text, _ in matched_evidence[:3]],
                evidence_locations=[location for _, location in matched_evidence[:3]],
                match_method="compound_requirement",
                reason=reason,
            )

    # Education requirements commonly combine a credential and a field while
    # the structured resume stores them in separate fields.
    if any(
        credential in normalized for credential in ("bachelor", "b tech", "b.tech")
    ) and any(
        field in normalized for field in ("computer science", "engineering", "related field")
    ):
        degree_matches = _find_matches(("bachelor", "b tech", "b.tech", "b s", "b sc"), evidence)
        field_matches = _find_matches(
            ("computer science", "software engineering", "engineering"),
            evidence,
        )
        if degree_matches and field_matches:
            matched = degree_matches[:1] + field_matches[:2]
            return RequirementMatch(
                requirement=requirement,
                normalized_requirement=normalized,
                priority=priority,
                status="covered",
                confidence=1.0,
                evidence=[item.text for item in matched],
                evidence_locations=[item.location for item in matched],
                match_method="structured_education",
                reason="The degree and field are present in separate structured education fields.",
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
        if term and key not in seen and not _is_context_requirement(term):
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
