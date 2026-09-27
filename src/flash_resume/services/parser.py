"""Resume ingestion and conversion service (PDF, text, Markdown -> MasterResume)."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Optional

import pypdf

from flash_resume.models.resume import MasterResume
from flash_resume.services.llm import LLMService


# Zero-width / invisible characters that garble extracted text.
_INVISIBLE = frozenset("​‌‍‎‏﻿")


def _clean_text(value: str) -> str:
    """Strip stray/garbled/decorative characters while keeping real content.

    Removes control characters, currency symbols (``$5M`` -> ``5M``), emoji and
    other decorative symbols, and zero-width characters. Keeps letters, digits,
    whitespace, and all meaningful punctuation — so ``C++``, ``C#``, ``F#``,
    ``.NET``, ``%``, decimals, hyphens, and ampersands survive intact.
    ``#``/``\\`` are deliberately kept: Typst renders runtime string values
    literally, so they cannot break compilation.
    """
    out = []
    for ch in value:
        o = ord(ch)
        if o < 32 and ch not in "\t\n\r":
            continue  # control characters (keep real newlines/tabs)
        if 0x7F <= o <= 0x9F:
            continue  # C1 control characters
        if ch in _INVISIBLE:
            continue
        if unicodedata.category(ch) in ("Sc", "So"):
            # Sc = currency symbols, So = emoji / decorative glyphs
            continue
        out.append(ch)
    return "".join(out)


def sanitize_master_resume(resume: MasterResume) -> MasterResume:
    """Backstop cleanup of stray symbols the LLM may have left in the resume.

    The LLM parse prompt already asks for clean text; this guarantees the JSON
    that reaches the Typst template cannot break compilation or carry garbled
    glyphs, even if the model misses some.
    """
    raw = resume.model_dump(mode="json")

    def _clean(obj):
        if isinstance(obj, str):
            return _clean_text(obj)
        if isinstance(obj, list):
            return [_clean(item) for item in obj]
        if isinstance(obj, dict):
            return {key: _clean(val) for key, val in obj.items()}
        return obj

    return MasterResume.model_validate(_clean(raw))


def extract_pdf_hyperlinks(file_path: Path) -> list[str]:
    """Return unique external URLs embedded as clickable /Link annotations.

    Resume PDFs (LaTeX, Word, Canva) often show a short placeholder —
    "GitHub", "LinkedIn", "Portfolio" — while the real URL lives only in the
    link annotation, invisible to plain text extraction. Reading the
    annotations recovers those URLs so the parser can fill contact.github /
    contact.linkedin / project links.
    """
    urls: list[str] = []
    seen: set[str] = set()
    try:
        reader = pypdf.PdfReader(str(file_path))
    except Exception:
        return urls
    for page in reader.pages:
        for annotation in page.get("/Annots") or []:
            try:
                annot = annotation.get_object()
                if annot.get("/Subtype") != "/Link":
                    continue
                action = annot.get("/A")
                if action is not None and action.get("/S") == "/URI":
                    uri = str(action.get("/URI") or "").strip()
                else:
                    # Some producers (e.g. Word) put the URI directly on the annotation.
                    uri = str(annot.get("/URI") or "").strip()
                if uri and uri not in seen:
                    seen.add(uri)
                    urls.append(uri)
            except Exception:
                # One malformed annotation must never kill the whole extraction.
                continue
    return urls


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

        # Recover clickable hyperlink targets that the text layer hides
        # (placeholder labels like "GitHub" whose real URL only lives in the
        # /Link annotation). mailto: targets also confirm the email address.
        links = extract_pdf_hyperlinks(file_path)
        mailto_addresses = [u[7:].strip() for u in links if u.lower().startswith("mailto:")]
        web_links = [u for u in links if not u.lower().startswith("mailto:")]
        if web_links:
            full_text += "\n\n[CLICKABLE HYPERLINKS FOUND IN THIS PDF]\n"
            full_text += "\n".join(f"- {url}" for url in web_links)
            full_text += "\n(Use these URLs for contact.github / contact.linkedin / contact.portfolio and project links when the visible text shows only a placeholder label.)"
        if mailto_addresses:
            emails = ", ".join(sorted(set(mailto_addresses)))
            full_text += f"\n[EMAIL FROM HYPERLINK: {emails}]"
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
    return sanitize_master_resume(llm.parse_resume_from_text(raw_text))

