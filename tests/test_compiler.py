"""Tests for Typst compiler service and page count verification."""

import copy
import json
from pathlib import Path
import pypdf
import pytest

from flash_resume.models.resume import MasterResume
from flash_resume.services.compiler import CompilerService
from flash_resume.services.tailor import extract_overflow, trim_resume_to_fit


def test_compiler_service_single_page(tmp_path: Path):
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    data = json.loads(example_path.read_text(encoding="utf-8"))
    resume = MasterResume(**data)

    compiler = CompilerService()
    output_pdf = tmp_path / "test_resume.pdf"

    pages, ms = compiler.compile(resume, output_pdf, max_pages=1)

    assert output_pdf.exists()
    assert output_pdf.stat().st_size > 1000
    assert pages == 1
    assert ms < 3000  # Should easily compile fast

    # Inspect PDF with pypdf
    reader = pypdf.PdfReader(str(output_pdf))
    assert len(reader.pages) == 1


def test_overflow_resume_is_trimmed_to_single_page(tmp_path: Path):
    """A bloated multi-page resume must still produce a verified 1-page PDF."""
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    data = json.loads(example_path.read_text(encoding="utf-8"))

    # Triple the experience entries and duplicate projects to force overflow
    exp = data["experience"][0]
    for i in range(2, 5):
        bloated = copy.deepcopy(exp)
        bloated["id"] = f"exp_{i}"
        bloated["company"] = f"Company {i}"
        data["experience"].append(bloated)
    proj = data["projects"][0]
    for i in range(3, 6):
        bloated_proj = copy.deepcopy(proj)
        bloated_proj["id"] = f"proj_{i}"
        bloated_proj["name"] = f"Project {i}"
        data["projects"].append(bloated_proj)

    resume = MasterResume(**data)
    compiler = CompilerService()
    output_pdf = tmp_path / "bloated_resume.pdf"

    fitted, pages, ms, trim_applied = trim_resume_to_fit(
        resume, compiler, output_pdf, max_pages=1
    )

    assert output_pdf.exists()
    assert pages == 1
    assert trim_applied is True
    reader = pypdf.PdfReader(str(output_pdf))
    assert len(reader.pages) == 1


def test_extract_overflow_returns_spilled_text(tmp_path: Path):
    """extract_overflow() must return the text spilled past the page budget."""
    example_path = Path(__file__).resolve().parent.parent / "examples" / "master_resume.json"
    data = json.loads(example_path.read_text(encoding="utf-8"))

    # Duplicate experience entries to force overflow at every density
    exp = data["experience"][0]
    for i in range(2, 5):
        bloated = copy.deepcopy(exp)
        bloated["id"] = f"exp_{i}"
        bloated["company"] = f"Company {i}"
        data["experience"].append(bloated)

    resume = MasterResume(**data)
    compiler = CompilerService()
    output_pdf = tmp_path / "overflow_extract.pdf"

    pages, _ = compiler.compile(resume, output_pdf, max_pages=1)
    assert pages > 1  # fixture overflows even at the tightest density

    spilled_text, spilled_words = extract_overflow(output_pdf, max_pages=1)

    assert spilled_text
    assert spilled_words > 10
    assert len(spilled_text.split()) == spilled_words

