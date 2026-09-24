"""Local companion FastAPI server enabling 1-click Chrome Extension tailoring."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from flash_resume.config import load_config, save_config
from flash_resume.models.resume import MasterResume
from flash_resume.services.tailor import TailorEngine

# Surface pipeline logs (model + timings) in the `fs serve` terminal.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(
    title="Flash Resume Local Companion",
    description="Local background API powering 1-click browser extension tailoring.",
    version="0.1.0",
)

# Enable CORS for browser extensions and local web callers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TailorRequest(BaseModel):
    jd: str = Field(description="Job description text extracted from page DOM")
    company: Optional[str] = Field(default=None, description="Optional detected company name")
    role: Optional[str] = Field(default=None, description="Optional detected role/title")


class ApiKeyRequest(BaseModel):
    api_key: str = Field(min_length=1, description="Gemini API key to persist in local config")


class GroqKeyRequest(BaseModel):
    api_key: str = Field(min_length=1, description="Groq API key to persist in local config")


class ProviderRequest(BaseModel):
    provider: str = Field(min_length=1, description="Provider to use: 'gemini' or 'groq'")


class OutputDirRequest(BaseModel):
    output_dir: str = Field(min_length=1, description="Folder where tailored resumes are saved")


class TailorResponse(BaseModel):
    success: bool
    company: str
    role: str
    ats_match_score: int
    matched_keywords: list[str]
    page_count: int
    pdf_path: str
    pdf_base64: str
    llm_time_ms: float
    compile_time_ms: float
    total_time_ms: float


@app.get("/api/status")
def get_status():
    """Return status of local Flash Resume engine and master resume."""
    cfg = load_config()
    resume_ready = False
    candidate_name = None

    if cfg.master_resume_path and Path(cfg.master_resume_path).exists():
        try:
            mr = MasterResume.model_validate_json(Path(cfg.master_resume_path).read_text(encoding="utf-8"))
            resume_ready = True
            candidate_name = mr.contact.name
        except Exception:
            pass

    return {
        "status": "online",
        "master_resume_configured": resume_ready,
        "candidate_name": candidate_name,
        "output_dir": cfg.output_dir,
        "has_api_key": bool(cfg.resolve_api_key()),
        "has_groq_key": bool(cfg.resolve_groq_api_key()),
        "provider": cfg.llm_provider,
    }


@app.post("/api/config/key")
def set_api_key(req: ApiKeyRequest):
    """Persist a Gemini API key supplied by the browser extension onboarding."""
    cfg = load_config()
    cfg.gemini_api_key = req.api_key.strip()
    save_config(cfg)
    return {"success": True, "detail": "API key saved to local Flash Resume config."}


@app.post("/api/config/groq-key")
def set_groq_key(req: GroqKeyRequest):
    """Persist a Groq API key supplied by the browser extension onboarding."""
    cfg = load_config()
    cfg.groq_api_key = req.api_key.strip()
    save_config(cfg)
    return {"success": True, "detail": "Groq API key saved to local Flash Resume config."}


@app.post("/api/config/provider")
def set_provider(req: ProviderRequest):
    """Switch the active LLM provider ('gemini' or 'groq')."""
    provider = req.provider.strip().lower()
    if provider not in ("gemini", "groq"):
        raise HTTPException(status_code=400, detail="Provider must be 'gemini' or 'groq'.")
    cfg = load_config()
    cfg.llm_provider = provider
    save_config(cfg)
    return {"success": True, "provider": provider}


@app.post("/api/config/output-dir")
def set_output_dir(req: OutputDirRequest):
    """Persist the folder where tailored resumes are saved (set during onboarding)."""
    cfg = load_config()
    cfg.output_dir = req.output_dir.strip()
    Path(cfg.output_dir).mkdir(parents=True, exist_ok=True)
    save_config(cfg)
    return {"success": True, "output_dir": cfg.output_dir}


@app.post("/api/tailor", response_model=TailorResponse)
def tailor_resume(req: TailorRequest):
    """Tailor resume directly from the extension's scraped job description."""
    cfg = load_config()
    if not cfg.master_resume_path or not Path(cfg.master_resume_path).exists():
        raise HTTPException(
            status_code=400,
            detail="Master resume is not configured. Please run 'fs init' in terminal first.",
        )

    try:
        engine = TailorEngine(cfg)
        result = engine.tailor(
            job_description=req.jd,
            company_override=req.company,
            role_override=req.role,
        )

        pdf_bytes = Path(result.pdf_path).read_bytes()
        pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

        return TailorResponse(
            success=True,
            company=result.plan.company,
            role=result.plan.role,
            ats_match_score=result.plan.ats_match_score,
            matched_keywords=result.plan.matched_keywords,
            page_count=result.page_count,
            pdf_path=result.pdf_path,
            pdf_base64=pdf_b64,
            llm_time_ms=result.llm_time_ms,
            compile_time_ms=result.compile_time_ms,
            total_time_ms=result.total_time_ms,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

