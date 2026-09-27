"""Tests for resume ingestion, special-character cleanup, and PDF path handling."""

from pathlib import Path

import typst

from flash_resume.models.resume import MasterResume
from flash_resume.services.parser import extract_raw_text_from_file, sanitize_master_resume


def _minimal_resume(**overrides) -> MasterResume:
    data = {
        "contact": {
            "name": "CHANDRAGIRI MANOJ KRISHNA",
            "email": "manoj@example.com",
            "phone": "+1 555 123 4567",
            "location": "San Francisco, CA",
        },
        "summary": "Raised $5M revenue — 30% YoY growth.",
        "skills": [{"category": "Languages", "items": ["C++", "C#", ".NET", "Python", "R&D"]}],
        "certifications": ["AWS Certified Developer - Associate"],
        "certification_groups": [
            {"category": "Cloud Certifications", "items": ["AWS Certified Developer - Associate"]}
        ],
    }
    data.update(overrides)
    return MasterResume.model_validate(data)


def test_sanitize_removes_currency_symbol_but_keeps_number():
    resume = _minimal_resume(summary="Raised $5M revenue — 30% YoY 💸 growth.")
    cleaned = sanitize_master_resume(resume)

    assert "$" not in cleaned.summary
    assert "5M" in cleaned.summary  # the metric survives
    assert "💸" not in cleaned.summary  # emoji stripped
    assert "30%" in cleaned.summary  # percentage kept


def test_sanitize_keeps_meaningful_punctuation():
    resume = _minimal_resume()
    cleaned = sanitize_master_resume(resume)

    assert "C++" in cleaned.skills[0].items
    assert "C#" in cleaned.skills[0].items
    assert ".NET" in cleaned.skills[0].items
    assert "R&D" in cleaned.skills[0].items
    assert "Python" in cleaned.skills[0].items


def test_sanitize_keeps_hash_and_backslash_but_removes_currency_and_control():
    resume = _minimal_resume(
        summary="C# and F# support \\ paths #1 / $100M ARR\u0000garbage"
    )
    cleaned = sanitize_master_resume(resume)

    # Meaningful punctuation is preserved (so C# / F# and hashes survive)...
    assert "C#" in cleaned.summary
    assert "F#" in cleaned.summary
    assert "#1" in cleaned.summary
    assert "\\" in cleaned.summary
    # ...while currency symbols and control chars are stripped.
    assert "$" not in cleaned.summary
    assert "100M" in cleaned.summary
    assert "\u0000" not in cleaned.summary


def test_sanitize_recurses_into_nested_groups():
    resume = _minimal_resume()
    resume.certification_groups[0].items = ["AWS $ cert", "GCP Certificate"]
    cleaned = sanitize_master_resume(resume)

    assert cleaned.certification_groups[0].items == ["AWS  cert", "GCP Certificate"]


def test_extract_raw_text_from_absolute_pdf_path(tmp_path: Path):
    """PDF ingestion must work with an absolute path, uppercase extension,
    and spaces in the filename."""
    source = tmp_path / "mini.typ"
    source.write_text(
        "#set page(width: 10cm, height: 10cm, margin: 0.6cm)\nJava Developer\nAWS Certified\nPython C++",
        encoding="utf-8",
    )
    pdf_bytes = typst.compile(source, root=tmp_path)
    pdf = tmp_path / "My Resume.PDF"
    pdf.write_bytes(pdf_bytes)

    text = extract_raw_text_from_file(pdf)

    assert "Java" in text
    assert "AWS" in text
    assert "C++" in text
