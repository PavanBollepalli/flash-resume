"""Resume ingestion and conversion service (PDF, text, Markdown -> MasterResume)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pypdf

from flash_resume.models.resume import MasterResume
from flash_resume.services.llm import LLMService


def extract_raw_text_from_file(file_path: Path) -> str:
    """Extract raw text from a PDF, TXT, or Markdown file."""
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        reader = pypdf.PdfReader(str(file_path))
        pages_text = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                pages_text.append(text)
        full_text = "\n\n".join(pages_text).strip()
        if not full_text:
            raise ValueError(
                "Could not extract text from this PDF. It may be a scanned image or empty. "
                "Please provide a text-based PDF, TXT, or Markdown file."
            )
        return full_text

    elif suffix in (".txt", ".md", ".json"):
        return file_path.read_text(encoding="utf-8").strip()

    else:
        # Fallback to reading as text
        try:
            return file_path.read_text(encoding="utf-8").strip()
        except Exception:
            raise ValueError(f"Unsupported file format '{suffix}'. Supported: .pdf, .txt, .md, .json")


def convert_resume_file_to_master(file_path: Path, llm: LLMService) -> MasterResume:
    """Read any candidate resume file and convert it into a structured MasterResume."""
    suffix = file_path.suffix.lower()

    # If it's already a JSON file matching MasterResume, load directly
    if suffix == ".json":
        try:
            return MasterResume.model_validate_json(file_path.read_text(encoding="utf-8"))
        except Exception:
            # If it's a JSON with different schema, parse with LLM
            pass

    raw_text = extract_raw_text_from_file(file_path)
    return llm.parse_resume_from_text(raw_text)

