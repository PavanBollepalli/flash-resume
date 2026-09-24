"""Configuration management for Flash Resume."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


class AppConfig(BaseModel):
    """User configuration preferences saved locally."""

    master_resume_path: Optional[str] = Field(
        default=None,
        description="Absolute path to the user's master_resume.json",
    )
    output_dir: str = Field(
        default_factory=lambda: str(Path.home() / "Resumes"),
        description="Default folder where compiled PDFs and diffs are saved",
    )
    default_model: str = Field(
        default="gemini-3.5-flash-lite",
        description="Model ID to use for AI ATS tailoring",
    )
    llm_provider: str = Field(
        default="gemini",
        description="LLM provider: 'gemini' or 'groq'",
    )
    groq_api_key: Optional[str] = Field(
        default=None,
        description="Groq API key (or set GROQ_API_KEY env var)",
    )
    groq_model: str = Field(
        default="openai/gpt-oss-20b",
        description="Groq model ID for fast tailoring",
    )

    def resolve_groq_api_key(self) -> Optional[str]:
        """Resolve Groq API key from environment variable or stored config."""
        return os.environ.get("GROQ_API_KEY") or self.groq_api_key
    max_pages: int = Field(
        default=1,
        description="Target maximum page count for the generated resume",
    )
    auto_clipboard: bool = Field(
        default=True,
        description="Whether to read JD from system clipboard by default",
    )
    gemini_api_key: Optional[str] = Field(
        default=None,
        description="Optional API key override (prefers GEMINI_API_KEY env var)",
    )

    def resolve_api_key(self) -> Optional[str]:
        """Resolve Gemini API key from environment variable or stored config."""
        return os.environ.get("GEMINI_API_KEY") or self.gemini_api_key


def get_config_dir() -> Path:
    """Return the configuration directory ~/.config/flash-resume/."""
    config_dir = Path.home() / ".config" / "flash-resume"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_file() -> Path:
    """Return path to config.json."""
    return get_config_dir() / "config.json"


def load_config() -> AppConfig:
    """Load configuration from disk or create default."""
    cfg_file = get_config_file()
    if cfg_file.exists():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            return AppConfig(**data)
        except Exception:
            return AppConfig()
    return AppConfig()


def save_config(config: AppConfig) -> Path:
    """Save configuration to disk."""
    cfg_file = get_config_file()
    cfg_file.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    return cfg_file

