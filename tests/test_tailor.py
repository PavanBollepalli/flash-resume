"""Tests for tailoring plan application and diff generation."""

import json
from pathlib import Path
import pytest

from flash_resume.models.resume import MasterResume
from flash_resume.models.tailoring import BulletEdit, SkillUpdate, TailorPlan
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
                replacement_text="Architected scalable telemetry microservices using FastAPI and Go, serving 12M+ monthly API requests with 99.98% uptime.",
                keywords_added=["telemetry", "gRPC"],
                word_count_delta=0,
            )
        ],
    )

    tailored = apply_tailor_plan(resume, plan)

    # Assert bullet was replaced
    assert tailored.experience[0].bullets[0] != original_bullet
    assert "telemetry microservices" in tailored.experience[0].bullets[0]

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

