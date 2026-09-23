"""Tests for Flash Resume Pydantic data models."""

from pathlib import Path
import json
import pytest

from flash_resume.models.resume import MasterResume
from flash_resume.models.tailoring import BulletEdit, TailorPlan


def test_master_resume_serialization():
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    data = json.loads(example_path.read_text(encoding="utf-8"))
    resume = MasterResume(**data)

    assert resume.contact.name == "Alex Chen"
    assert len(resume.skills) >= 3
    assert len(resume.experience) >= 2
    assert len(resume.projects) >= 2
    assert resume.experience[0].company == "CloudScale Technologies"


def test_tailor_plan_model():
    plan = TailorPlan(
        company="Stripe",
        role="Backend Engineer",
        ats_match_score=92,
        matched_keywords=["FastAPI", "PostgreSQL", "Redis"],
        bullet_edits=[
            BulletEdit(
                section="Experience",
                item_id="exp_cloudscale_swe",
                bullet_index=0,
                original_text="Built APIs with Python and databases.",
                replacement_text="Engineered high-throughput REST APIs using FastAPI and PostgreSQL.",
                keywords_added=["FastAPI", "PostgreSQL"],
                word_count_delta=2,
            )
        ],
    )

    assert plan.company == "Stripe"
    assert plan.ats_match_score == 92
    assert len(plan.bullet_edits) == 1
    assert plan.bullet_edits[0].word_count_delta == 2

