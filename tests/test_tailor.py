"""Tests for tailoring plan application and diff generation."""

import json
from pathlib import Path
import pytest

from flash_resume.models.resume import MasterResume
from flash_resume.models.tailoring import BulletEdit, JDKeywords, SkillUpdate, TailorPlan
from flash_resume.services.tailor import (
    apply_tailor_plan,
    build_evidence_map,
    generate_diff_markdown,
    sanitize_tailor_plan,
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
    assert "**JD Coverage:** 2/2 supported requirements" in diff_md
    assert "Kubernetes" in diff_md
    assert "64.2 ms" in diff_md


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

