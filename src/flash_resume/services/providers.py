"""Assemble the LLM service for the configured provider.

Three providers ship — ``gemini`` (quality), ``groq`` (speed) and ``hybrid``,
which routes each pipeline stage to the provider that measurably wins it (see
``services/hybrid.py``). This module is the single place that turns a provider
string into a service object, so the engine and the CLI's setup/import flows
cannot drift apart; before it existed the CLI hardcoded the Gemini key, which
left Groq-configured users unable to import a resume.
"""

from __future__ import annotations

from typing import Any

from flash_resume.config import AppConfig

# Provider strings accepted in config.json and by /api/config/provider.
PROVIDERS = ("gemini", "groq", "hybrid")

# Human-readable names for console output and status messages.
PROVIDER_LABELS = {"gemini": "Gemini", "groq": "Groq", "hybrid": "Hybrid"}


def _gemini_service(config: AppConfig):
    """Build the Gemini service, or fail with a message naming the missing key."""
    from flash_resume.services.llm import LLMService

    api_key = config.resolve_api_key()
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set.")
    return LLMService(api_key=api_key, model=config.default_model)


def _groq_service(config: AppConfig):
    """Build the Groq service, or fail with a message naming the missing key."""
    from flash_resume.services.groq_llm import GroqLLMService

    api_key = config.resolve_groq_api_key()
    if not api_key:
        raise ValueError("GROQ_API_KEY is not set.")
    return GroqLLMService(api_key=api_key, model=config.groq_model)


def build_llm_service(config: AppConfig) -> tuple[Any, str]:
    """Return ``(service, label)`` for the configured provider.

    Raises ``ValueError`` when the provider is unknown or when a key the
    provider needs is missing. Hybrid needs both keys, and says which one is
    absent rather than leaving the user to guess.
    """
    provider = (config.llm_provider or "gemini").strip().lower()

    if provider == "groq":
        return _groq_service(config), PROVIDER_LABELS["groq"]

    if provider == "hybrid":
        # Name the missing key explicitly: hybrid is the one provider where
        # "the key isn't set" is ambiguous, and it is only reachable after the
        # user has deliberately configured two providers.
        if not config.resolve_groq_api_key():
            raise ValueError("Hybrid mode needs both keys: GROQ_API_KEY is not set.")
        if not config.resolve_api_key():
            raise ValueError("Hybrid mode needs both keys: GEMINI_API_KEY is not set.")

        from flash_resume.services.hybrid import HybridLLMService

        return HybridLLMService(fast=_groq_service(config), reasoning=_gemini_service(config)), PROVIDER_LABELS["hybrid"]

    if provider != "gemini":
        raise ValueError(
            f"Unknown provider {config.llm_provider!r}. Use one of: {', '.join(PROVIDERS)}."
        )

    return _gemini_service(config), PROVIDER_LABELS["gemini"]
