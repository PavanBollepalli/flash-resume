"""LLM Service orchestrating Gemini Flash structured ATS resume tailoring."""

from __future__ import annotations

import json
import logging
from typing import Optional

from google import genai
from google.genai import types

from flash_resume.models.job import JobAnalysis
from flash_resume.models.resume import MasterResume
from flash_resume.models.tailoring import JDKeywords, TailorPlan


logger = logging.getLogger("flash_resume.llm")


TAILOR_SYSTEM_INSTRUCTION = """You are Flash Resume's Precision ATS Optimization Engine.

Your task is to tailor a candidate's Master Resume to a specific Job Description (JD).
Your focus is SURGICAL, TARGETED KEYWORD INTEGRATION with ZERO page overflow and 100% FACTUAL INTEGRITY.

STRICT PRINCIPLES & CONSTRAINTS:
1. TRUTHFULNESS IS LAW:
   - NEVER fabricate employers, roles, degrees, certifications, or unmentioned technologies.
   - Only integrate skills, tools, and domain keywords that align with the candidate's existing background.
2. WORD & CHARACTER BUDGET (CRITICAL TO PREVENT PAGE OVERFLOW):
   - For every modified bullet point, the replacement MUST be within ±2 words of the original bullet.
   - CHARACTER BUDGET: The replacement MUST NOT exceed the total character length of the original bullet by more than 15 characters (watch out for long words that cause extra line wraps).
   - Do NOT lengthen bullets. Do NOT add extra sentences or clauses that wrap onto new lines.
3. SURGICAL MODIFICATION ONLY:
    - Select 1 to 2 high-impact bullet points to rephrase using the JD's exact terminology.
    - Do not rewrite a bullet merely to produce an edit. Only modify it when the change adds one or more high-priority JD keywords supported by the master resume and materially improves relevance.
    - Preserve relevant keywords already present in the original bullet. If a bullet already matches the JD adequately, return a BulletEdit with action 'skip'. Omitting that bullet edit is also valid.
   - Update Skills categories with supported exact terms.
4. METADATA DETECTION:
   - Infer the target company name and job title from the JD text if not provided.
   - Estimate an ATS match score (0-100) reflecting keyword alignment.
"""


JD_EXTRACT_INSTRUCTION = """You are Flash Resume's ATS Keyword Extraction Engine.

Read the job description and extract the ATS-critical keywords a recruiter or
ATS system would scan for, so a resume can be tailored to match. Split them into:

1. required_keywords - hard requirements and core technical skills (e.g.
   languages, frameworks, tools, domain expertise) directly demanded by the JD.
2. preferred_keywords - nice-to-haves or 'preferred qualifications'.

Only include terms that genuinely appear or are clearly implied in the JD.
Output ONLY valid JSON matching the JDKeywords schema.
"""


CONDENSE_SYSTEM_INSTRUCTION = """You are Flash Resume's Content Condensation Engine.
A candidate's tailored resume overflows the ONE-PAGE budget. Rewrite the
candidate's MasterResume so it fits a single page while keeping the most
impactful, JD-relevant content and 100% FACTUAL INTEGRITY.

STRICT RULES:
1. NEVER fabricate employers, roles, degrees, metrics, or technologies. Only
   condense what is already present — cut, tighten, and reword — never invent.
2. Fit a single A4 page: target roughly 450-550 words of body content total.
3. Prune lowest-value content first, in this priority order:
   - Shorten the summary to 1-2 punchy sentences (drop fluff).
   - Reduce each experience/project to its 2-4 strongest, metric-backed bullets.
   - Drop the least relevant / least recent experience or project entries.
   - Tighten wordy bullets to under ~20 words, keeping hard numbers.
4. Keep the contact block byte-for-byte unchanged, and keep all real
   companies, roles, dates, and quantifiable metrics intact.
5. Preserve ATS keywords already integrated by the tailoring pass.
6. Return the complete MasterResume schema with the same field structure.
7. When MEASURED OVERFLOW is provided, prioritize condensing exactly the
   spilled content shown and hit the stated word-reclamation target rather
   than rewriting the whole resume from scratch.

Output ONLY valid JSON matching the MasterResume schema.
"""


class LLMService:
    """Service wrapping Google Gemini Flash for structured resume tailoring."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-3.5-flash-lite"):
        self.api_key = api_key
        self.model = model
        self._client: Optional[genai.Client] = None

    @property
    def client(self) -> genai.Client:
        """Lazily initialize the Gemini client."""
        if self._client is None:
            if not self.api_key:
                raise ValueError(
                    "GEMINI_API_KEY is not set.\n"
                    "Set it in your environment: export GEMINI_API_KEY='your-key' (or $env:GEMINI_API_KEY='your-key' in PowerShell)\n"
                    "or run 'fs init' to configure it."
                )
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def extract_jd_keywords(self, job_description: str) -> JDKeywords:
        """LLM call #1: extract ATS-relevant keywords from the job description.

        Returns an empty JDKeywords on any failure; callers fall back to the
        static EVIDENCE_TERMS map when the result carries no keywords.
        """
        prompt = f"JOB DESCRIPTION:\n\n{job_description}\n\nExtract ATS keywords into the JDKeywords schema."
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=JD_EXTRACT_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=JDKeywords,
                    max_output_tokens=1024,
                    temperature=0.1,
                ),
            )
            if isinstance(response.parsed, JDKeywords):
                return response.parsed
            data = json.loads(response.text or "{}")
            return JDKeywords(**data)
        except Exception as exc:
            logger.warning("JD keyword extraction failed (%s); using static term list", exc)
            return JDKeywords()

    def generate_tailor_plan(
        self,
        resume: MasterResume,
        job_description: str,
        company_override: Optional[str] = None,
        role_override: Optional[str] = None,
        supported_terms: Optional[list[str]] = None,
        unsupported_terms: Optional[list[str]] = None,
        jd_keywords: Optional[JDKeywords] = None,
    ) -> TailorPlan:
        """Call Gemini Flash to generate a structured TailorPlan."""
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
"""

        if jd_keywords and (jd_keywords.required_keywords or jd_keywords.preferred_keywords):
            prompt += (
                "\nJD EXTRACTED KEYWORDS:\n"
                f"Required: {', '.join(jd_keywords.required_keywords)}\n"
                f"Preferred: {', '.join(jd_keywords.preferred_keywords)}\n"
            )

        prompt += f"""
LOCAL EVIDENCE MAP:
Supported terms present in the master resume: {", ".join(supported_terms or []) or "None"}
JD terms not supported by the master resume: {", ".join(unsupported_terms or []) or "None"}

Analyze the Job Description, extract core technical keywords, and generate a surgical, word-count-constrained TailorPlan.
"""
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=TAILOR_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=TailorPlan,
                max_output_tokens=2048,
                temperature=0.2,
            ),
        )

        if response.parsed:
            plan = response.parsed
            if isinstance(plan, TailorPlan):
                if company_override:
                    plan.company = company_override
                if role_override:
                    plan.role = role_override
                return plan

        # Fallback parsing if response.parsed wasn't populated as model
        text = response.text or "{}"
        data = json.loads(text)
        if company_override:
            data["company"] = company_override
        if role_override:
            data["role"] = role_override
        return TailorPlan(**data)

    def parse_resume_from_text(self, raw_text: str) -> MasterResume:
        """Parse raw resume text (from PDF or text) into a structured MasterResume."""
        parse_instruction = (
            "You are Flash Resume's Expert Resume Ingestion Engine. "
            "Convert the candidate's resume text into the exact MasterResume structured schema. "
            "Preserve 100% of the candidate's real metrics, dates, companies, bullet points, and skills. "
            "Assign clean IDs like 'exp_1', 'exp_2' to experience items and 'proj_1', 'proj_2' to projects."
        )

        prompt = f"CANDIDATE RAW RESUME TEXT:\n\n{raw_text}\n\nParse into MasterResume schema."
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=parse_instruction,
                response_mime_type="application/json",
                response_schema=MasterResume,
                temperature=0.1,
            ),
        )

        if response.parsed and isinstance(response.parsed, MasterResume):
            return response.parsed

        text = response.text or "{}"
        return MasterResume.model_validate_json(text)

    def condense_resume(
        self,
        resume: MasterResume,
        job_description: str = "",
        max_pages: int = 1,
        overflow_text: str = "",
        overflow_words: int = 0,
    ) -> MasterResume:
        """Ask the LLM to rewrite a resume that overflows to fit one page.

        Preserves factual integrity while tightening wording and pruning
        lowest-value content. When ``overflow_text``/``overflow_words`` are
        provided (measured from the compiled PDF), the prompt targets that
        exact spilled content. Returns a new MasterResume; caller recompiles.
        """
        payload = {
            "contact": resume.contact.model_dump(mode="json"),
            "summary": resume.summary,
            "skills": [skill.model_dump(mode="json") for skill in resume.skills],
            "experience": [item.model_dump(mode="json") for item in resume.experience],
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
            "certifications": [c for c in (resume.certifications or [])],
            "awards": [a for a in (resume.awards or [])],
        }

        overflow_block = ""
        if overflow_words > 0:
            target = int(overflow_words * 1.25) + 10
            overflow_block = (
                "\n\nMEASURED OVERFLOW (from the compiled PDF):\n"
                f"The resume currently spills past page {max_pages}. "
                f"Approximately {target} words must be reclaimed.\n"
                "Text currently on the overflow page(s):\n"
                f'"""{overflow_text}"""\n'
                "Prefer tightening or removing exactly this spilled content. "
                f"Reclaim at least {target} words while keeping the most "
                "JD-relevant, metric-backed content."
            )

        prompt = (
            f"CONDENSE THIS RESUME TO FIT {max_pages} PAGE(S):\n\n"
            f"{json.dumps(payload, separators=(',', ':'))}\n\n"
            "Keep the contact block unchanged. Prune lowest-value content and "
            "tighten wording while preserving all real metrics, companies, and dates."
            f"{overflow_block}"
            "\nReturn ONLY valid JSON matching the MasterResume schema."
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=CONDENSE_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=MasterResume,
                temperature=0.2,
            ),
        )

        if response.parsed and isinstance(response.parsed, MasterResume):
            return response.parsed
        text = response.text or "{}"
        return MasterResume.model_validate_json(text)


