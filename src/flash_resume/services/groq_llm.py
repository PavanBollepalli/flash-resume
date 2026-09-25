"""Groq LLM Service — fast provider option for ATS resume tailoring.

Groq's LPU inference serves open models (e.g. OpenAI gpt-oss-20b) at very high
token rates, making the tailoring step near-instant compared to Gemini's
thinking models. Structured output is enforced via a JSON-mode system
prompt plus post-parse validation into the TailorPlan schema.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from openai import OpenAI

from flash_resume.models.resume import MasterResume
from flash_resume.models.tailoring import TailorPlan
from flash_resume.services.llm import TAILOR_SYSTEM_INSTRUCTION

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqLLMService:
    """Service wrapping the Groq OpenAI-compatible API for fast tailoring."""

    def __init__(self, api_key: Optional[str] = None, model: str = "openai/gpt-oss-20b"):
        self.api_key = api_key
        self.model = model
        self._client: Optional[OpenAI] = None

    @property
    def client(self) -> OpenAI:
        """Lazily initialize the Groq client."""
        if self._client is None:
            if not self.api_key:
                raise ValueError(
                    "GROQ_API_KEY is not set.\n"
                    "Set it in your environment: export GROQ_API_KEY='your-key' (or $env:GROQ_API_KEY='your-key' in PowerShell)\n"
                    "or paste it in the extension setup."
                )
            self._client = OpenAI(api_key=self.api_key, base_url=GROQ_BASE_URL)
        return self._client

    def generate_tailor_plan(
        self,
        resume: MasterResume,
        job_description: str,
        company_override: Optional[str] = None,
        role_override: Optional[str] = None,
        supported_terms: Optional[list[str]] = None,
        unsupported_terms: Optional[list[str]] = None,
    ) -> TailorPlan:
        """Call Groq to generate a structured TailorPlan (same contract as LLMService)."""
        resume_payload = {
            "summary": resume.summary,
            "skills": [skill.model_dump(mode="json") for skill in resume.skills],
            "experience": [
                {
                    "id": item.id,
                    "company": item.company,
                    "role": item.role,
                    "bullets": item.bullets,
                }
                for item in resume.experience
            ],
            "projects": [
                {
                    "id": item.id,
                    "name": item.name,
                    "technologies": item.technologies,
                    "bullets": item.bullets,
                }
                for item in resume.projects
            ],
            "education": [
                {"degree": item.degree, "field_of_study": item.field_of_study}
                for item in resume.education
            ],
        }
        prompt = f"""
CANDIDATE MASTER RESUME:
{json.dumps(resume_payload, separators=(",", ":"))}

TARGET JOB DESCRIPTION:
{job_description}

OVERRIDES:
Company: {company_override or "Infer from Job Description"}
Role: {role_override or "Infer from Job Description"}

LOCAL EVIDENCE MAP:
Supported terms present in the master resume: {", ".join(supported_terms or []) or "None"}
JD terms not supported by the master resume: {", ".join(unsupported_terms or []) or "None"}

Analyze the Job Description, extract core technical keywords, and generate a surgical, word-count-constrained TailorPlan.
Return ONLY the JSON object, no commentary.
"""
        schema_hint = json.dumps(TailorPlan.model_json_schema(), indent=2)

        response = self.client.chat.completions.create(
            model=self.model,
            # gpt-oss-* are reasoning models: reasoning tokens consume the
            # completion budget and temperature is pinned at 1.0, so give
            # max_completion_tokens real headroom instead of a tight max_tokens.
            max_completion_tokens=8192,
            reasoning_effort="low",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": TAILOR_SYSTEM_INSTRUCTION},
                {
                    "role": "system",
                    "content": f"Respond with a single JSON object matching this JSON Schema:\n{schema_hint}",
                },
                {"role": "user", "content": prompt},
            ],
        )

        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError(
                f"Groq returned an empty response for model {self.model}. "
                "The model likely exhausted its token budget on reasoning — "
                "try again, or switch the Groq model in your config."
            )
        # Groq sometimes emits JSON with `+`-signed numbers like `+1` — strip
        # those to make valid JSON before parsing.
        text = re.sub(r":\s*\+(\d+)", r": \1", text)
        data = json.loads(text)
        if company_override:
            data["company"] = company_override
        if role_override:
            data["role"] = role_override
        return TailorPlan(**data)
