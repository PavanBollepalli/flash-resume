"""Tests for the hybrid provider's per-stage routing and its fallback rule."""

import pytest

from flash_resume.config import AppConfig
from flash_resume.models.tailoring import CoreEditPlan, JDKeywords, ProjectEditPlan, TailorPlan
from flash_resume.services.hybrid import HybridLLMService
from flash_resume.services.providers import build_llm_service


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch):
    """Keys resolve from the environment first, so tests must not inherit any."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)


class FakeService:
    """Records which calls it received, and answers with canned results."""

    def __init__(self, model: str, keywords: JDKeywords | None = None, boom: bool = False):
        self.model = model
        self.keywords = keywords if keywords is not None else JDKeywords()
        self.boom = boom
        self.calls: list[str] = []

    def _record(self, name: str):
        self.calls.append(name)
        if self.boom:
            raise RuntimeError(f"{self.model} exploded")

    def extract_jd_keywords(self, job_description: str) -> JDKeywords:
        self._record("extract_jd_keywords")
        return self.keywords

    def generate_core_edit_plan(self, **kwargs) -> CoreEditPlan:
        self._record("generate_core_edit_plan")
        return CoreEditPlan(company="Acme", role="SWE")

    def generate_project_edit_plan(self, **kwargs) -> ProjectEditPlan:
        self._record("generate_project_edit_plan")
        return ProjectEditPlan()

    def generate_tailor_plan(self, **kwargs) -> TailorPlan:
        self._record("generate_tailor_plan")
        return TailorPlan(company="Acme", role="SWE")

    def condense_resume(self, **kwargs):
        self._record("condense_resume")
        return kwargs["resume"]

    def parse_resume_from_text(self, raw_text: str):
        self._record("parse_resume_from_text")
        return raw_text


@pytest.fixture
def fast():
    return FakeService("fast-model", keywords=JDKeywords(required_keywords=["Python"]))


@pytest.fixture
def reasoning():
    return FakeService("reasoning-model", keywords=JDKeywords(required_keywords=["Fallback"]))


def test_extraction_goes_to_the_fast_provider(fast, reasoning):
    hybrid = HybridLLMService(fast=fast, reasoning=reasoning)

    keywords = hybrid.extract_jd_keywords("a job description")

    assert keywords.required_keywords == ["Python"]
    assert fast.calls == ["extract_jd_keywords"]
    assert reasoning.calls == []


def test_planning_stays_on_the_reasoning_provider(fast, reasoning):
    """Planning on the fast model measured 4.4x slower, so it must not move."""
    hybrid = HybridLLMService(fast=fast, reasoning=reasoning)

    hybrid.generate_core_edit_plan(resume=None, job_description="jd")
    hybrid.generate_project_edit_plan(resume=None, job_description="jd")
    hybrid.generate_tailor_plan(resume=None, job_description="jd")
    hybrid.parse_resume_from_text("raw resume text")

    assert reasoning.calls == [
        "generate_core_edit_plan",
        "generate_project_edit_plan",
        "generate_tailor_plan",
        "parse_resume_from_text",
    ]
    # The fast provider is never asked to plan, in any spelling of the call.
    assert fast.calls == []


def test_condense_stays_on_the_reasoning_provider(fast, reasoning):
    hybrid = HybridLLMService(fast=fast, reasoning=reasoning)
    sentinel = object()

    assert hybrid.condense_resume(resume=sentinel) is sentinel
    assert reasoning.calls == ["condense_resume"]
    assert fast.calls == []


def test_empty_fast_extraction_falls_back_to_reasoning(fast, reasoning):
    """Groq reports failure by returning empty keywords, never by raising."""
    fast.keywords = JDKeywords()
    hybrid = HybridLLMService(fast=fast, reasoning=reasoning)

    keywords = hybrid.extract_jd_keywords("a job description")

    assert keywords.required_keywords == ["Fallback"]
    assert reasoning.calls == ["extract_jd_keywords"]


def test_preferred_only_extraction_is_usable(fast, reasoning):
    """A posting with only nice-to-haves is still a real extraction."""
    fast.keywords = JDKeywords(preferred_keywords=["Kubernetes"])
    hybrid = HybridLLMService(fast=fast, reasoning=reasoning)

    assert hybrid.extract_jd_keywords("jd").preferred_keywords == ["Kubernetes"]
    assert reasoning.calls == []


def test_raising_fast_provider_falls_back_to_reasoning(fast, reasoning):
    fast.boom = True
    hybrid = HybridLLMService(fast=fast, reasoning=reasoning)

    assert hybrid.extract_jd_keywords("jd").required_keywords == ["Fallback"]
    assert reasoning.calls == ["extract_jd_keywords"]


def test_model_label_names_both_models():
    hybrid = HybridLLMService(
        fast=FakeService("openai/gpt-oss-20b"), reasoning=FakeService("gemini-3.5-flash-lite")
    )
    assert hybrid.model == "gemini-3.5-flash-lite + openai/gpt-oss-20b (extract)"


def test_split_plan_runner_accepts_the_hybrid_service(fast, reasoning):
    """The engine's concurrent runner must drive both halves through one object."""
    from flash_resume.models.resume import ContactInfo, MasterResume
    from flash_resume.services.tailor import generate_split_plan

    hybrid = HybridLLMService(fast=fast, reasoning=reasoning)
    resume = MasterResume(
        contact=ContactInfo(name="A Candidate", email="a@b.c", phone="+1", location="Remote")
    )

    plan = generate_split_plan(
        llm=hybrid,
        resume=resume,
        job_description="a job description",
        company_override=None,
        role_override=None,
        supported_terms=[],
        unsupported_terms=[],
        jd_keywords=JDKeywords(required_keywords=["Python"]),
        interview_mode=False,
    )

    # Both specialist calls reached the reasoning provider, via the hybrid object.
    assert reasoning.calls == ["generate_core_edit_plan", "generate_project_edit_plan"]
    assert fast.calls == []
    assert isinstance(plan, TailorPlan)


# -- provider assembly ------------------------------------------------------


def _race_for_client(service) -> list:
    """Start two threads on ``service.client`` and return the clients they got.

    Construction is deliberately slow, so without a lock both threads pass the
    ``is None`` check before either finishes building. The threads are released
    together so they really do overlap.
    """
    import threading

    start = threading.Barrier(2)
    clients: list = []
    errors: list = []

    def touch():
        start.wait(timeout=5)
        try:
            clients.append(service.client)
        except Exception as exc:  # pragma: no cover - only on regression
            errors.append(exc)

    threads = [threading.Thread(target=touch) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not errors, errors
    return clients


def test_concurrent_first_use_builds_one_shared_gemini_client(monkeypatch):
    """The two specialist calls can warm a cold client at the same time.

    Both threads used to see ``None`` and build their own client; the loser's
    was dropped from the service while that thread was still sending through it,
    which fails as "the client has been closed" and silently downgrades the run
    to the legacy single-plan call. Hybrid mode is where it surfaced, because
    extraction runs on Groq and so the concurrent pair touches this client first.
    """
    import time

    from flash_resume.services.llm import LLMService

    built: list = []

    class FakeClient:
        def __init__(self, **_kwargs):
            time.sleep(0.15)
            built.append(self)

    monkeypatch.setattr("flash_resume.services.llm.genai.Client", FakeClient)

    clients = _race_for_client(LLMService(api_key="test-key"))

    assert len(built) == 1
    assert clients == [built[0], built[0]]


def test_concurrent_first_use_builds_one_shared_groq_client(monkeypatch):
    """Same race, same fix, on the provider whose specialists both run there."""
    import time

    from flash_resume.services.groq_llm import GroqLLMService

    built: list = []

    class FakeClient:
        def __init__(self, **_kwargs):
            time.sleep(0.15)
            built.append(self)

    monkeypatch.setattr("flash_resume.services.groq_llm.OpenAI", FakeClient)

    clients = _race_for_client(GroqLLMService(api_key="test-key"))

    assert len(built) == 1
    assert clients == [built[0], built[0]]


def _config(provider: str, gemini: str | None = "g", groq: str | None = "k") -> AppConfig:
    return AppConfig(llm_provider=provider, gemini_api_key=gemini, groq_api_key=groq)


def test_engine_builds_a_hybrid_service_from_config():
    from flash_resume.services.tailor import TailorEngine

    engine = TailorEngine(_config("hybrid"))

    assert isinstance(engine.llm, HybridLLMService)
    assert engine.provider_label == "Hybrid"
    # The engine's model label drives the CLI banner, so it must name both models.
    assert engine.model == engine.llm.model


def test_hybrid_provider_needs_both_keys():
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        build_llm_service(_config("hybrid", gemini="g", groq=None))
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        build_llm_service(_config("hybrid", gemini=None, groq="k"))


def test_hybrid_provider_composes_both_services():
    from flash_resume.services.groq_llm import GroqLLMService
    from flash_resume.services.hybrid import HybridLLMService as Hybrid
    from flash_resume.services.llm import LLMService

    service, label = build_llm_service(_config("hybrid"))

    assert isinstance(service, Hybrid)
    assert isinstance(service.fast, GroqLLMService)
    assert isinstance(service.reasoning, LLMService)
    assert label == "Hybrid"
    assert "extract" in service.model


def test_single_provider_modes_are_unaffected():
    from flash_resume.services.groq_llm import GroqLLMService
    from flash_resume.services.llm import LLMService

    gemini, gemini_label = build_llm_service(_config("gemini"))
    groq, groq_label = build_llm_service(_config("groq"))

    assert isinstance(gemini, LLMService) and gemini_label == "Gemini"
    assert isinstance(groq, GroqLLMService) and groq_label == "Groq"


def test_missing_key_and_unknown_provider_are_reported():
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        build_llm_service(_config("gemini", gemini=None))
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        build_llm_service(_config("groq", groq=None))
    with pytest.raises(ValueError, match="Unknown provider"):
        build_llm_service(_config("openai"))


def test_provider_string_is_case_insensitive():
    from flash_resume.services.hybrid import HybridLLMService as Hybrid

    service, _ = build_llm_service(_config(" Hybrid "))
    assert isinstance(service, Hybrid)


def test_api_status_reports_hybrid_readiness(monkeypatch):
    """has_required_keys is the one question the extension popup asks."""
    from fastapi.testclient import TestClient

    from flash_resume.services import server

    monkeypatch.setattr(server, "load_config", lambda: _config("hybrid", gemini="g", groq="k"))
    body = TestClient(server.app).get("/api/status").json()
    assert body["provider"] == "hybrid"
    assert body["has_required_keys"] is True

    # Half a hybrid setup is not a working engine.
    monkeypatch.setattr(server, "load_config", lambda: _config("hybrid", gemini="g", groq=None))
    assert TestClient(server.app).get("/api/status").json()["has_required_keys"] is False


def test_has_required_keys_matches_each_provider():
    from flash_resume.services.server import has_required_keys

    assert has_required_keys(_config("hybrid", gemini="g", groq="k")) is True
    assert has_required_keys(_config("hybrid", gemini=None, groq="k")) is False
    assert has_required_keys(_config("hybrid", gemini="g", groq=None)) is False
    assert has_required_keys(_config("gemini", gemini="g", groq=None)) is True
    assert has_required_keys(_config("gemini", gemini=None, groq="k")) is False
    assert has_required_keys(_config("groq", gemini=None, groq="k")) is True
    assert has_required_keys(_config("groq", gemini="g", groq=None)) is False
    # An unknown provider cannot run, so it must not read as ready.
    assert has_required_keys(_config("mystery", gemini="g", groq="k")) is False
