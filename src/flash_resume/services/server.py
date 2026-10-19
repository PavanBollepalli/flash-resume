"""Local companion FastAPI server enabling 1-click Chrome Extension tailoring."""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from flash_resume import __version__
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
    version=__version__,
)

# Enable CORS for browser extensions and local web callers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PrivateNetworkAccessMiddleware:
    """Let a public https page (the extension's floating button) reach this
    localhost server. Chrome's Private Network Access requires a preflight that
    answers `Access-Control-Allow-Private-Network: true`; CORSMiddleware alone
    does not add that header, so without this a page-side fetch is blocked."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            async def send_with_headers(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    pna = b"access-control-allow-private-network"
                    if not any(k.lower() == pna for k, _ in headers):
                        headers.append((pna, b"true"))
                    message["headers"] = headers
                await send(message)

            await self.inner(scope, receive, send_with_headers)
        else:
            await self.inner(scope, receive, send)


# Registered after CORSMiddleware so this stays outermost and can decorate the
# OPTIONS preflight response CORS generates (short-circuited before reaching the
# route handlers).
app.add_middleware(PrivateNetworkAccessMiddleware)


class TailorRequest(BaseModel):
    jd: str = Field(description="Job description text extracted from page DOM")
    company: Optional[str] = Field(default=None, description="Optional detected company name")
    role: Optional[str] = Field(default=None, description="Optional detected role/title")
    mode: Literal["truthful", "interview_prep"] = Field(
        default="truthful",
        description="Truthful tailoring or clearly labeled interview preparation",
    )


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
    preparation_skills: list[str]
    page_count: int
    pdf_path: str
    downloads_path: str
    downloads_filename: str
    pdf_base64: str
    llm_time_ms: float
    compile_time_ms: float
    total_time_ms: float


def _downloads_dir() -> Path:
    """Resolve the user's real Downloads folder, surviving OneDrive relocation.

    The browser no longer drives the download (that surfaced a Save-As dialog);
    the server writes the tailored PDF here directly, so we want the path the OS
    actually treats as "Downloads" rather than a guess.
    """
    try:
        import ctypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        # FOLDERID_Downloads = {374DE290-123F-4565-9164-39C4925E467B}
        guid = GUID()
        guid.Data1 = 0x374DE290
        guid.Data2 = 0x123F
        guid.Data3 = 0x4565
        guid.Data4 = (0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B)
        pidl = ctypes.c_void_p()
        res = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(pidl)
        )
        if res == 0:
            try:
                path = Path(ctypes.wstring_at(pidl))
                ctypes.windll.ole32.CoTaskMemFree(pidl)
                return path
            except Exception:
                pass
    except Exception:
        pass
    return Path.home() / "Downloads"


def _sanitize_filename_part(value: str) -> str:
    """Make a string safe to embed in a filename: strip illegal chars, collapse spaces."""
    cleaned = "".join(ch for ch in value if ch not in '\\/:*?"<>|' and ord(ch) >= 32)
    return " ".join(cleaned.split()).strip()


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
            interview_mode=req.mode == "interview_prep",
            source="extension",
        )

        pdf_bytes = Path(result.pdf_path).read_bytes()
        pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

        # The browser no longer calls chrome.downloads — that surfaced a Save-As
        # dialog. Instead the server writes a clean, human-named copy of the PDF
        # straight into the user's Downloads folder, the same way it already writes
        # the diff.md artifacts. Name it "<FirstName>_<Company>.pdf".
        first_name = "Resume"
        try:
            mr = MasterResume.model_validate_json(
                Path(cfg.master_resume_path).read_text(encoding="utf-8")
            )
            full = (mr.contact.name or "").strip()
            if full:
                first_name = full.split()[0]
        except Exception:
            pass
        company_slug = _sanitize_filename_part(result.plan.company) or "Resume"
        downloads_dir = _downloads_dir()
        try:
            downloads_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        downloads_filename = f"{first_name}_{company_slug}.pdf"
        downloads_path = downloads_dir / downloads_filename
        try:
            downloads_path.write_bytes(pdf_bytes)
        except Exception:
            # The Downloads copy is a convenience; never fail tailoring over it.
            pass

        return TailorResponse(
            success=True,
            company=result.plan.company,
            role=result.plan.role,
            ats_match_score=result.plan.ats_match_score,
            matched_keywords=result.plan.matched_keywords,
            preparation_skills=result.plan.preparation_skills,
            page_count=result.page_count,
            pdf_path=result.pdf_path,
            downloads_path=str(downloads_path),
            downloads_filename=downloads_filename,
            pdf_base64=pdf_b64,
            llm_time_ms=result.llm_time_ms,
            compile_time_ms=result.compile_time_ms,
            total_time_ms=result.total_time_ms,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Application history / resume retrieval
# ---------------------------------------------------------------------------

def _fmt_date(value: str) -> str:
    """Return a YYYY-MM-DD date from a SQLite timestamp string."""
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d")
    except ValueError:
        return str(value)[:10]


def _application_payload(app: dict) -> dict:
    return {
        "id": app["id"],
        "date": _fmt_date(app["created_at"]),
        "company": app["company"],
        "role": app["role"],
        "ats_score": app["ats_score"],
        "filename": app["pdf_filename"],
        "mode": app["mode"],
        "source": app["source"],
        "status": app["status"],
    }


class HistoryStatusRequest(BaseModel):
    status: Literal["applied", "interview", "offered"]


@app.get("/api/history")
def get_history(
    limit: int = 10,
    offset: int = 0,
    company: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
):
    """Return paginated, filterable application history (newest first)."""
    from flash_resume.services.tracking import (
        count_applications,
        get_recent_applications,
    )

    rows = get_recent_applications(
        limit=limit,
        offset=offset,
        company_filter=company,
        role_filter=role,
        status_filter=status,
    )
    total = count_applications(
        company_filter=company, role_filter=role, status_filter=status
    )
    return {
        "applications": [_application_payload(r) for r in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@app.get("/api/resume/{application_id}")
def get_resume_pdf(application_id: int):
    """Re-download or view the PDF for a past application."""
    from flash_resume.services.tracking import get_application_by_id

    app = get_application_by_id(application_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    pdf_path = Path(load_config().output_dir) / app["pdf_filename"]
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF no longer exists on disk")
    return Response(
        content=pdf_path.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{pdf_path.name}"'},
    )


@app.get("/api/resume/{application_id}/diff")
def get_resume_diff(application_id: int):
    """View/export the diff.md report for a past application."""
    from flash_resume.services.tracking import get_application_by_id

    app = get_application_by_id(application_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    base = Path(load_config().output_dir)
    diff_path = base / (Path(app["pdf_filename"]).stem + ".diff.md")
    if not diff_path.exists():
        raise HTTPException(status_code=404, detail="diff.md no longer exists on disk")
    return Response(
        content=diff_path.read_text(encoding="utf-8"),
        media_type="text/plain",
        headers={"Content-Disposition": f'inline; filename="{diff_path.name}"'},
    )


@app.patch("/api/history/{application_id}")
def set_history_status(application_id: int, req: HistoryStatusRequest):
    """Update an application's pipeline status (applied | interview | offered)."""
    from flash_resume.services.tracking import (
        get_application_by_id,
        update_application_status,
    )

    if not get_application_by_id(application_id):
        raise HTTPException(status_code=404, detail="Application not found")
    update_application_status(application_id, req.status)
    return {"success": True, "status": req.status}


@app.get("/api/history/stats")
def get_history_stats(company: Optional[str] = None, role: Optional[str] = None):
    """One-glance pipeline stats for the history header."""
    from flash_resume.services.tracking import get_stats

    return get_stats(company_filter=company, role_filter=role)
