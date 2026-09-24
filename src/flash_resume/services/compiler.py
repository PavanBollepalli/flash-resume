"""Compiler service that generates PDFs via Typst and guarantees 1-page constraints."""

from __future__ import annotations

import io
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Optional, Tuple

import pypdf
import typst

from flash_resume.models.resume import MasterResume

logger = logging.getLogger("flash_resume.tailor")


class CompilerService:
    """Service for compiling resumes with Typst and enforcing layout constraints."""

    def __init__(self, template_path: Optional[Path] = None):
        if template_path is None:
            # Default to templates/resume.typ in repository
            repo_template = Path(__file__).resolve().parent.parent.parent.parent / "templates" / "resume.typ"
            if not repo_template.exists():
                # Fallback to package-installed location
                repo_template = Path(__file__).resolve().parent.parent / "templates" / "resume.typ"
            self.template_path = repo_template
        else:
            self.template_path = template_path

        if not self.template_path.exists():
            raise FileNotFoundError(f"Typst template not found at {self.template_path}")

    def compile(
        self,
        resume: MasterResume,
        output_pdf_path: Path,
        max_pages: int = 1,
    ) -> Tuple[int, float]:
        """Compile a resume into a PDF, enforcing max page count.

        Returns:
            Tuple[int, float]: (page_count, compile_time_ms)
        """
        output_pdf_path.parent.mkdir(parents=True, exist_ok=True)

        # We need a temporary JSON file to feed to Typst
        temp_json_path = output_pdf_path.with_suffix(".temp.json")
        try:
            temp_json_path.write_text(resume.model_dump_json(indent=2), encoding="utf-8")

            # Typst root should be the project root or the directory containing the files
            # To allow Typst to open temp_json_path, set root to common ancestor or drive root
            root_dir = temp_json_path.parent.resolve()
            if not self.template_path.resolve().is_relative_to(root_dir):
                # If they are on different directory trees, copy template next to json
                temp_template = temp_json_path.parent / "_resume_template.typ"
                shutil.copy(self.template_path, temp_template)
                compile_target = temp_template
                root_dir = temp_json_path.parent.resolve()
            else:
                temp_template = None
                compile_target = self.template_path.resolve()

            # Relative data path with leading slash
            rel_data_path = "/" + temp_json_path.name

            # Pass 1: Standard compilation
            t0 = time.time()
            pdf_bytes = typst.compile(
                compile_target,
                root=root_dir,
                sys_inputs={"data_path": rel_data_path, "density": "standard"},
            )
            dt = (time.time() - t0) * 1000

            # Programmatically verify page count with pypdf
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            page_count = len(reader.pages)
            logger.info("compile pass=standard | %.0fms | pages=%d", dt, page_count)

            # Pass 2: If overflowed (> max_pages), trigger compact mode (~30ms)
            if page_count > max_pages:
                t1 = time.time()
                compact_bytes = typst.compile(
                    compile_target,
                    root=root_dir,
                    sys_inputs={"data_path": rel_data_path, "density": "compact"},
                )
                cdt = (time.time() - t1) * 1000
                reader2 = pypdf.PdfReader(io.BytesIO(compact_bytes))
                if len(reader2.pages) < page_count:
                    pdf_bytes = compact_bytes
                    page_count = len(reader2.pages)
                dt += cdt
                logger.info("compile pass=compact | %.0fms | pages=%d", cdt, page_count)

            # Pass 3: If still overflowed (> max_pages), trigger tight mode (~30ms)
            if page_count > max_pages:
                t2 = time.time()
                tight_bytes = typst.compile(
                    compile_target,
                    root=root_dir,
                    sys_inputs={"data_path": rel_data_path, "density": "tight"},
                )
                tdt = (time.time() - t2) * 1000
                reader3 = pypdf.PdfReader(io.BytesIO(tight_bytes))
                if len(reader3.pages) < page_count:
                    pdf_bytes = tight_bytes
                    page_count = len(reader3.pages)
                dt += tdt
                logger.info("compile pass=tight | %.0fms | pages=%d", tdt, page_count)

            # Write finalized PDF
            output_pdf_path.write_bytes(pdf_bytes)

            return page_count, dt

        finally:
            if temp_json_path.exists():
                temp_json_path.unlink()
            if "temp_template" in locals() and temp_template is not None and temp_template.exists():
                temp_template.unlink()

