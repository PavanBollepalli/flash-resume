"""Tests for tailoring plan application and diff generation."""

import json
from pathlib import Path
import pytest

from flash_resume.models.resume import MasterResume
from flash_resume.models.tailoring import (
    BulletEdit,
    BulletOrder,
    CoreTailorPlan,
    ContentPriority,
    EvidenceAssignment,
    JDKeywords,
    ProjectTailorPlan,
    SkillUpdate,
    TailorPlan,
)
from flash_resume.services.tailor import (
    apply_tailor_plan,
    build_evidence_map,
    generate_diff_markdown,
    highlight_keywords,
    sanitize_tailor_plan,
    generate_split_plan,
)
from flash_resume.services.evidence import (
    align_resume_to_jd,
    coverage_score,
    evaluate_requirements,
)
from flash_resume.services.validator import validate_bullet_length


def test_validate_bullet_length_rejects_large_reduction():
    is_valid, word_delta, _ = validate_bullet_length(
        "Built reliable production systems with measurable business outcomes.",
        "Built reliable systems.",
    )

    assert not is_valid
    assert word_delta < -2


def test_apply_tailor_plan_skips_invalid_bullet_edit():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    original_bullet = resume.experience[0].bullets[0]
    plan = TailorPlan(
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id=resume.experience[0].id,
                bullet_index=0,
                original_text=original_bullet,
                replacement_text="Short rewrite.",
            )
        ]
    )

    tailored = apply_tailor_plan(resume, plan)

    assert tailored.experience[0].bullets[0] == original_bullet


def test_apply_tailor_plan_skips_explicit_skip_action():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    original_bullet = resume.experience[0].bullets[0]
    plan = TailorPlan(
        bullet_edits=[
            BulletEdit(
                action="skip",
                section="Experience",
                item_id=resume.experience[0].id,
                bullet_index=0,
                original_text=original_bullet,
                replacement_text=original_bullet,
            )
        ]
    )

    tailored = apply_tailor_plan(resume, plan)

    assert tailored.experience[0].bullets[0] == original_bullet


def test_apply_tailor_plan_reorders_only_complete_bullet_permutations():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    item = resume.experience[0]
    original = list(item.bullets)
    plan = TailorPlan(
        bullet_orders=[
            BulletOrder(section="Experience", item_id=item.id, bullet_indices=list(reversed(range(len(original))))),
            BulletOrder(section="Experience", item_id=item.id, bullet_indices=[0]),
        ]
    )

    tailored = apply_tailor_plan(resume, plan)

    assert tailored.experience[0].bullets == list(reversed(original))


def test_role_aware_fields_are_reported():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    plan = TailorPlan(
        content_priorities=[
            ContentPriority(
                section="Projects",
                item_id="rag-project",
                priority="high",
                supported_keywords=["Python"],
                reason="Direct backend evidence",
            )
        ],
        evidence_assignments=[
            EvidenceAssignment(
                requirement="Python",
                section="projects",
                item_id="rag-project",
                bullet_index=0,
                strength="strong",
            )
        ],
    )

    report = generate_diff_markdown(resume, resume, plan, page_count=1, compile_time_ms=1.0)

    assert "## Role-Focused Content Plan" in report
    assert "## Evidence Assignments" in report


def test_split_plan_merges_disjoint_core_and_project_outputs():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))

    class FakeLLM:
        def generate_core_tailor_plan(self, **kwargs):
            return CoreTailorPlan(summary_edit="Backend-focused summary")

        def generate_project_tailor_plan(self, **kwargs):
            return ProjectTailorPlan(
                content_priorities=[
                    ContentPriority(
                        section="Projects",
                        item_id="proj_1",
                        priority="high",
                        reason="Strong backend evidence",
                    )
                ]
            )

        def generate_tailor_plan(self, **kwargs):
            raise AssertionError("legacy fallback should not be used")

    plan = generate_split_plan(
        FakeLLM(), resume, "backend role", None, None, [], [], JDKeywords(), False
    )

    assert plan.summary_edit == "Backend-focused summary"
    assert plan.content_priorities[0].item_id == "proj_1"


def test_split_plan_falls_back_when_specialist_call_fails():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))

    class FakeLLM:
        def generate_core_tailor_plan(self, **kwargs):
            raise RuntimeError("provider failure")

        def generate_project_tailor_plan(self, **kwargs):
            return ProjectTailorPlan()

        def generate_tailor_plan(self, **kwargs):
            return TailorPlan(summary_edit="Fallback summary")

    plan = generate_split_plan(
        FakeLLM(), resume, "backend role", None, None, [], [], JDKeywords(), False
    )

    assert plan.summary_edit == "Fallback summary"


def test_split_plan_uses_safe_metadata_fallbacks():
    class FakeLLM:
        def generate_core_tailor_plan(self, **kwargs):
            return CoreTailorPlan(company="Infer", role="Infer")

        def generate_project_tailor_plan(self, **kwargs):
            return ProjectTailorPlan()

        def generate_tailor_plan(self, **kwargs):
            raise AssertionError("legacy fallback should not be used")

    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    plan = generate_split_plan(
        FakeLLM(), resume, "backend role", None, None, [], [], JDKeywords(), False
    )

    assert plan.company == "Company"
    assert plan.role == "Software Engineer"


def test_evidence_gate_rejects_unsupported_skill_claim():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    plan = TailorPlan(
        skill_updates=[
            SkillUpdate(
                category="Backend",
                original_items=resume.skills[3].items,
                updated_items=resume.skills[3].items + ["Pydantic"],
                added_keywords=["Pydantic"],
            )
        ]
    )

    sanitized = sanitize_tailor_plan(plan, resume, "Python FastAPI Pydantic Azure OpenAI")

    assert "Pydantic" not in sanitized.skill_updates[0].updated_items
    assert "Pydantic" in sanitized.missing_keywords


def test_evidence_map_intersects_jd_and_resume():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))

    supported, unsupported = build_evidence_map(
        resume,
        "Python FastAPI LangChain Azure OpenAI Pydantic RAG Docker",
    )

    assert "Python" in supported
    assert "FastAPI" in supported
    assert "LangChain" in unsupported


def test_synonym_requirement_is_covered_and_aligns_bullet_to_jd_wording():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    resume.experience[0].bullets[0] = "Built services with PostgreSQL"
    keywords = JDKeywords(required_keywords=["Postgres"])

    assessment = evaluate_requirements(resume, keywords)
    aligned = align_resume_to_jd(resume, assessment)

    assert assessment[0].status == "covered"
    assert assessment[0].match_method == "synonym"
    assert aligned.experience[0].bullets[0] == "Built services with Postgres"


def test_alignment_does_not_flatten_more_specific_resume_wording():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    resume.experience[0].bullets[0] = "Built services with Python 3.11"
    keywords = JDKeywords(required_keywords=["Python"])

    assessment = evaluate_requirements(resume, keywords)
    aligned = align_resume_to_jd(resume, assessment)

    assert assessment[0].status == "covered"
    assert aligned.experience[0].bullets[0] == "Built services with Python 3.11"


def test_apply_tailor_plan():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    data = json.loads(example_path.read_text(encoding="utf-8"))
    resume = MasterResume(**data)

    original_bullet = resume.experience[0].bullets[0]
    # Build a replacement that passes validate_bullet_length: identical word
    # count and near-identical typographic width (single-word swap).
    words = original_bullet.split()
    replacement_words = list(words)
    replacement_words[1] = "scalable"
    replacement_text = " ".join(replacement_words)

    plan = TailorPlan(
        company="Datadog",
        role="Backend Engineer",
        ats_match_score=95,
        matched_keywords=["FastAPI", "Telemetry", "gRPC"],
        skill_updates=[
            SkillUpdate(
                category="Languages",
                original_items=resume.skills[0].items,
                updated_items=resume.skills[0].items + ["Rust"],
                added_keywords=["Rust"],
            )
        ],
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id=resume.experience[0].id,
                bullet_index=0,
                original_text=original_bullet,
                replacement_text=replacement_text,
                keywords_added=["scalable"],
                word_count_delta=0,
            )
        ],
    )

    tailored = apply_tailor_plan(resume, plan)

    # Assert bullet was replaced
    assert tailored.experience[0].bullets[0] == replacement_text
    assert tailored.experience[0].bullets[0] != original_bullet

    # Assert skill was added
    assert "Rust" in tailored.skills[0].items

    # Assert original was not mutated
    assert resume.experience[0].bullets[0] == original_bullet
    assert "Rust" not in resume.skills[0].items


def test_generate_diff_markdown():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    data = json.loads(example_path.read_text(encoding="utf-8"))
    resume = MasterResume(**data)

    plan = TailorPlan(
        company="Google",
        role="Site Reliability Engineer",
        ats_match_score=88,
        matched_keywords=["Kubernetes", "Prometheus"],
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id=resume.experience[0].id,
                bullet_index=0,
                original_text="Old bullet text here.",
                replacement_text="New tailored bullet text here.",
                keywords_added=["Kubernetes"],
                word_count_delta=0,
            )
        ],
    )

    diff_md = generate_diff_markdown(
        original=resume,
        tailored=resume,
        plan=plan,
        page_count=1,
        compile_time_ms=64.2,
    )

    assert "Flash Resume ATS Tailoring Report: Google - Site Reliability Engineer" in diff_md
    assert "**Evidence-based ATS Score:** 88%" in diff_md
    assert "## Requirement Evidence Matrix" in diff_md
    assert "Kubernetes" in diff_md
    assert "64.2 ms" in diff_md


def test_generate_diff_markdown_keeps_multiline_values_in_tables():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    plan = TailorPlan(
        company="Company",
        role="Backend Engineer",
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id="exp",
                bullet_index=0,
                original_text="Used Python | SQL\nwith tests",
                replacement_text="Used Python | SQL with integration tests",
            )
        ],
    )

    report = generate_diff_markdown(resume, resume, plan, 1, 10.0)
    lines = report.splitlines()

    assert "| Section | Original | Tailored | Word Change |" in lines
    assert any("Used Python \\| SQL with tests" in line for line in lines)
    assert any("Used Python \\| SQL with integration tests" in line for line in lines)
    assert "## Modified Bullet Points" in lines


def test_sanitize_tailor_plan_reports_rejected_bullet_edit():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    original = resume.experience[0].bullets[0]
    plan = TailorPlan(
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id=resume.experience[0].id,
                bullet_index=0,
                original_text=original,
                replacement_text="Invented Kubernetes platform leadership experience.",
            )
        ]
    )

    sanitized = sanitize_tailor_plan(
        plan,
        resume,
        "Python Kubernetes",
        jd_keywords=JDKeywords(required_keywords=["Python", "Kubernetes"]),
    )

    assert not sanitized.bullet_edits
    assert len(sanitized.rejected_bullet_edits) == 1
    assert "supported JD evidence" in sanitized.rejected_bullet_edits[0].reason

    report = generate_diff_markdown(resume, resume, sanitized, 1, 10.0)
    assert "## Rejected Bullet Points" in report
    assert "Invented Kubernetes platform leadership experience." in report


def test_sanitize_tailor_plan_separates_skips_from_rejections():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    original = resume.experience[0].bullets[0]
    plan = TailorPlan(
        bullet_edits=[
            BulletEdit(
                action="skip",
                section="Experience",
                item_id=resume.experience[0].id,
                bullet_index=0,
                original_text=original,
                replacement_text=original,
            )
        ]
    )

    sanitized = sanitize_tailor_plan(plan, resume, "Python", JDKeywords(required_keywords=["Python"]))

    assert not sanitized.rejected_bullet_edits
    assert len(sanitized.skipped_bullet_edits) == 1
    report = generate_diff_markdown(resume, resume, sanitized, 1, 10.0)
    assert "**Rejected Bullet Edits:** 0" in report
    assert "**Skipped Bullet Edits:** 1" in report
    assert "## Skipped Bullet Points" in report


def test_sanitize_tailor_plan_rejects_generic_skill_and_fact_loss():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    original = "Reduced latency by 95% by optimizing the production pipeline."
    resume.experience[0].bullets[0] = original
    plan = TailorPlan(
        skill_updates=[
            SkillUpdate(
                category="Backend",
                original_items=["Python"],
                updated_items=["Python", "backend stack"],
                added_keywords=["backend stack"],
            )
        ],
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id=resume.experience[0].id,
                bullet_index=0,
                original_text=original,
                replacement_text="Built reliable backend services.",
            )
        ],
    )

    sanitized = sanitize_tailor_plan(
        plan,
        resume,
        "Python backend stack",
        JDKeywords(required_keywords=["Python", "backend stack"]),
    )

    assert "backend stack" not in sanitized.skill_updates[0].updated_items
    assert not sanitized.bullet_edits
    assert "concrete metric" in sanitized.rejected_bullet_edits[0].reason


def test_sanitize_tailor_plan_allows_supported_phrase_expansion():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    original = (
        "Authored reusable **Python** query modules with docstrings and **unit tests**, "
        "eliminating repetitive ad-hoc analysis scripts used by the analytics team"
    )
    replacement = (
        "Authored reusable **Python** query modules with docstrings and "
        "**unit and integration tests**, eliminating repetitive ad-hoc analysis scripts "
        "for the analytics team"
    )
    resume.experience[0].bullets[0] = original
    plan = TailorPlan(
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id=resume.experience[0].id,
                bullet_index=0,
                original_text=original,
                replacement_text=replacement,
            )
        ]
    )

    sanitized = sanitize_tailor_plan(
        plan,
        resume,
        "Python unit and integration tests",
        JDKeywords(required_keywords=["Python", "unit and integration tests"]),
    )

    assert len(sanitized.bullet_edits) == 1
    assert not sanitized.rejected_bullet_edits


def test_sanitize_tailor_plan_allows_preserved_ci_outcome():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    original = (
        "Shipped with PDF import, **unit tests**, and **GitHub Actions CI/CD** "
        "that publishes to PyPI on version tags"
    )
    replacement = (
        "Built robust **unit and integration tests** and **Git**-based "
        "**GitHub Actions CI/CD** publishing to PyPI on version tags"
    )
    resume.projects[0].bullets[0] = original
    plan = TailorPlan(
        bullet_edits=[
            BulletEdit(
                section="Projects",
                item_id=resume.projects[0].id,
                bullet_index=0,
                original_text=original,
                replacement_text=replacement,
            )
        ]
    )

    sanitized = sanitize_tailor_plan(
        plan,
        resume,
        "unit and integration tests Git GitHub Actions CI/CD",
        JDKeywords(required_keywords=["unit and integration tests", "Git"]),
    )

    assert len(sanitized.bullet_edits) == 1


def test_sanitize_tailor_plan_applies_reported_role_keyword_rewrites():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    experience_original = (
        "Authored reusable **Python query modules** with docstrings and **unit tests**, "
        "eliminating repetitive ad-hoc analysis scripts used by the analytics team"
    )
    project_original = (
        "Shipped with PDF import, **unit tests**, and **GitHub Actions CI/CD** "
        "that publishes to PyPI on version tags"
    )
    resume.experience[0].bullets[0] = experience_original
    resume.projects[0].bullets[0] = project_original
    plan = TailorPlan(
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id=resume.experience[0].id,
                bullet_index=0,
                original_text=experience_original,
                replacement_text=(
                    "Authored robust **Python** modules with **unit and integration tests** "
                    "and **REST** capabilities, eliminating ad-hoc analysis scripts"
                ),
            ),
            BulletEdit(
                section="Projects",
                item_id=resume.projects[0].id,
                bullet_index=0,
                original_text=project_original,
                replacement_text=(
                    "Shipped with PDF import, **unit and integration tests**, and "
                    "**Git** CI/CD pipelines that publish to PyPI on version tags"
                ),
            ),
        ]
    )

    sanitized = sanitize_tailor_plan(
        plan,
        resume,
        "Python REST unit and integration tests Git",
        JDKeywords(
            required_keywords=["Python", "REST", "unit and integration tests", "Git"]
        ),
    )

    assert len(sanitized.bullet_edits) == 2
    assert not sanitized.rejected_bullet_edits


def test_sanitize_tailor_plan_deduplicates_evidence_assignments():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    plan = TailorPlan(
        evidence_assignments=[
            EvidenceAssignment(requirement="Python", section="skills", strength="strong"),
            EvidenceAssignment(
                requirement="Python",
                section="experience",
                item_id=resume.experience[0].id,
                bullet_index=0,
                strength="strong",
            ),
        ]
    )

    sanitized = sanitize_tailor_plan(plan, resume, "Python", JDKeywords(required_keywords=["Python"]))

    assert len(sanitized.evidence_assignments) == 1


def test_sanitize_tailor_plan_rejects_unsupported_summary_claim():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    plan = TailorPlan(
        summary_edit=(
            "Python engineer building AI agents and LLM-powered workflows with REST APIs."
        )
    )

    sanitized = sanitize_tailor_plan(
        plan,
        resume,
        "AI agents and LLM-powered workflows",
        JDKeywords(required_keywords=["AI agents and LLM-powered workflows"]),
    )

    assert sanitized.summary_edit is None
    assert sanitized.summary_edit_rejected_reason


def test_evidence_map_with_llm_keywords_includes_terms_outside_static_list():
    """LLM-extracted keywords bypass EVIDENCE_TERMS, so niche terms (LLVM,
    compilers) are honored instead of silently dropped."""
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))

    jd_keywords = JDKeywords(
        required_keywords=["LLVM", "C++", "Python", "Toolchains", "Compilers"],
        preferred_keywords=["Kubernetes"],
    )

    supported, unsupported = build_evidence_map(resume, "google jd", jd_keywords)

    # C++ (from "C/C++") and Python are genuinely in the resume -> supported.
    assert "Python" in supported
    assert "C++" in supported
    # LLVM/Compilers are required but absent from the resume -> unsupported
    # (and none of these are in EVIDENCE_TERMS, proving the static list is bypassed).
    assert "LLVM" in unsupported
    assert "Compilers" in unsupported


def test_sanitize_tailor_plan_uses_llm_keywords_as_evidence_gate():
    """A bullet edit that introduces a resume-backed term outside the static
    EVIDENCE_TERMS list should survive sanitization when the keyword came from
    LLM extraction (previously it would be dropped because the term was not in
    the hardcoded list)."""
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    resume = MasterResume.model_validate_json(example_path.read_text(encoding="utf-8"))
    # HandShake project bullets do not mention Django, so we can add it cleanly.
    original_bullet = resume.projects[1].bullets[0]

    plan = TailorPlan(
        bullet_edits=[
            BulletEdit(
                action="replace",
                section="Projects",
                item_id=resume.projects[1].id,
                bullet_index=0,
                original_text=original_bullet,
                replacement_text=f"{original_bullet} Django",
            )
        ]
    )

    # Django is in the resume (supported) but not in EVIDENCE_TERMS (the old
    # static list) — the LLM-extracted keywords must make it honor this edit.
    jd_keywords = JDKeywords(required_keywords=["Django", "LLVM"])
    sanitized = sanitize_tailor_plan(plan, resume, "backend jd", jd_keywords)

    assert [e.replacement_text for e in sanitized.bullet_edits][0].endswith(" Django")
    # LLVM stays flagged as a missing keyword rather than being silently dropped.
    assert "LLVM" in sanitized.missing_keywords


def test_evidence_matcher_recognizes_resume_equivalents():
    """Coverage must be based on real evidence, not only copied JD wording."""
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    data = json.loads(example_path.read_text(encoding="utf-8"))
    data["skills"].append({"category": "Databases", "items": ["PostgreSQL", "pgvector"]})
    data["experience"][0]["bullets"] += [
        "Authored reusable Python modules with docstrings and unit tests.",
        "Reduced SQL query execution time by 25% through composite indexing.",
    ]
    data["projects"][0]["bullets"].append(
        "Built a RAG retrieval pipeline with hybrid vector search and API integrations."
    )
    resume = MasterResume(**data)
    keywords = JDKeywords(required_keywords=[
        "testing", "reusable code", "maintainable code", "SQL databases",
        "vector databases", "performance optimization", "AI workflows", "API integrations",
    ])

    assessment = evaluate_requirements(resume, keywords)
    by_requirement = {item.requirement: item for item in assessment}

    assert by_requirement["testing"].status == "covered"
    assert by_requirement["reusable code"].status == "covered"
    assert by_requirement["maintainable code"].status == "covered"
    assert by_requirement["SQL databases"].status == "covered"
    assert by_requirement["vector databases"].status == "covered"
    assert by_requirement["performance optimization"].status == "covered"
    assert by_requirement["AI workflows"].status == "covered"
    assert by_requirement["API integrations"].status == "covered"
    assert all(item.evidence and item.evidence_locations for item in assessment)


def test_evidence_score_weights_preferred_and_partial_requirements():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    data = json.loads(example_path.read_text(encoding="utf-8"))
    data["skills"].append({"category": "Cloud", "items": ["AWS", "Docker", "GitHub Actions"]})
    resume = MasterResume(**data)
    assessment = evaluate_requirements(
        resume,
        JDKeywords(
            required_keywords=["Python", "cloud deployment"],
            preferred_keywords=["LangGraph"],
        ),
    )
    by_requirement = {item.requirement: item for item in assessment}

    assert by_requirement["Python"].status == "covered"
    assert by_requirement["cloud deployment"].status == "partial"
    assert by_requirement["LangGraph"].status == "unsupported"
    # (1.0 + 0.5) / (1.0 + 1.0 + 0.35) = 63.8%, rounded to 64.
    assert coverage_score(assessment) == 64


def test_highlight_keywords_merges_with_ingest_bold_markers():
    """JD-keyword bolding must coexist with ingest-time **bold** markers.

    A bullet that already carries ingest-time bold (e.g. ``**RAG pipeline**``)
    must still get JD-keyword bolding on its plain segments (FastAPI, Docker),
    without nesting, corrupting, or dropping the existing markers.
    """
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    data = json.loads(example_path.read_text(encoding="utf-8"))
    data["experience"][0]["bullets"] = [
        "Built a **RAG pipeline** with FastAPI and CI/CD, cutting latency by **95%**.",
    ]
    resume = MasterResume(**data)

    highlighted = highlight_keywords(resume, ["FastAPI", "CI/CD", "Docker"])

    bullet = highlighted.experience[0].bullets[0]
    # Existing ingest markers preserved, unaltered.
    assert "**RAG pipeline**" in bullet
    assert "**95%**" in bullet
    # JD keywords added into plain segments of the same bullet.
    assert "**FastAPI**" in bullet
    assert "**CI/CD**" in bullet
    # No nested/dangling markers: split on '**' must leave even-length, paired spans.
    segments = bullet.split("**")
    assert len(segments) % 2 == 1  # ends with plain text, i.e. balanced pairs
    for i in range(len(segments)):
        assert "**" not in segments[i]  # no double-wrapped segments
