"""Tests for Typst compiler service and page count verification."""

import json
from pathlib import Path
import pypdf
import pytest

from flash_resume.models.resume import MasterResume
from flash_resume.services.compiler import CompilerService


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

