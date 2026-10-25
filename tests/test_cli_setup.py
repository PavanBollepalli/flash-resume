"""Tests for `fs setup`'s provider step.

The wizard is the only user-facing way to select a provider, so the mapping
from a keystroke to ``cfg.llm_provider`` — and the keys hybrid asks for — is
worth pinning down. Every side effect (saving config, autostart registration,
extension copying, the init wizard) is stubbed: this must never touch the
machine running the tests.
"""

import pytest

from flash_resume import cli
from flash_resume.config import AppConfig


class FakePrompt:
    """Stand-in for rich's Prompt that answers from a script and records asks."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []  # (question, kwargs) per ask

    def ask(self, question, **kwargs):
        self.calls.append((question, kwargs))
        return self.answers.pop(0)

    @property
    def questions(self):
        return [question for question, _ in self.calls]


@pytest.fixture
def wizard(monkeypatch, tmp_path):
    """Run setup_cmd against a stub config, returning what the wizard captured."""
    state = {}

    def install(answers):
        prompt = FakePrompt(answers)
        cfg = state["cfg"]
        monkeypatch.setattr(cli, "Prompt", prompt)
        monkeypatch.setattr(cli, "load_config", lambda: cfg)
        monkeypatch.setattr(cli, "save_config", lambda c: tmp_path / "config.json")
        monkeypatch.setattr(cli, "get_config_file", lambda: tmp_path / "config.json")
        monkeypatch.setattr(cli, "_default_output_dir", lambda: tmp_path / "out")
        monkeypatch.setattr(cli, "init_cmd", lambda: None)
        monkeypatch.setattr(cli.autostart, "install", lambda: "stub")
        monkeypatch.setattr(cli.autostart, "engine_is_running", lambda: True)
        monkeypatch.setattr(cli.extension, "install_to", lambda: tmp_path / "ext")
        cli.setup_cmd()
        return prompt

    state["run"] = install
    state["cfg"] = AppConfig(gemini_api_key=None, groq_api_key=None)
    return state


def test_choice_three_selects_hybrid_and_asks_for_both_keys(wizard):
    prompt = wizard["run"](["3", "gsk_groq", "gem_gemini"])

    cfg = wizard["cfg"]
    assert cfg.llm_provider == "hybrid"
    assert cfg.groq_api_key == "gsk_groq"
    assert cfg.gemini_api_key == "gem_gemini"
    assert len(prompt.questions) == 3


def test_choice_one_and_two_still_ask_for_one_key_each(wizard):
    prompt = wizard["run"](["2", "gsk_groq"])
    assert wizard["cfg"].llm_provider == "groq"
    assert wizard["cfg"].groq_api_key == "gsk_groq"
    assert len(prompt.questions) == 2  # provider + one key

    wizard["cfg"] = AppConfig(gemini_api_key=None, groq_api_key=None)
    prompt = wizard["run"](["1", "gem_gemini"])
    assert wizard["cfg"].llm_provider == "gemini"
    assert wizard["cfg"].gemini_api_key == "gem_gemini"
    assert len(prompt.questions) == 2


def test_empty_key_answer_keeps_the_stored_key(wizard):
    """Re-running setup to change the provider must not wipe an existing key."""
    wizard["cfg"] = AppConfig(llm_provider="gemini", gemini_api_key="keep_me", groq_api_key="keep_groq")

    wizard["run"](["3", "", ""])

    assert wizard["cfg"].llm_provider == "hybrid"
    assert wizard["cfg"].groq_api_key == "keep_groq"
    assert wizard["cfg"].gemini_api_key == "keep_me"


def test_default_choice_reflects_the_configured_provider(wizard):
    """Re-running setup to change an unrelated step must not switch providers."""
    wizard["cfg"] = AppConfig(llm_provider="hybrid", gemini_api_key="g", groq_api_key="k")
    prompt = wizard["run"](["3", "", ""])
    assert prompt.calls[0][1]["default"] == "3"

    wizard["cfg"] = AppConfig(llm_provider="groq", groq_api_key="k")
    prompt = wizard["run"](["2", ""])
    assert prompt.calls[0][1]["default"] == "2"

    wizard["cfg"] = AppConfig(llm_provider="gemini", gemini_api_key="g")
    prompt = wizard["run"](["1", ""])
    assert prompt.calls[0][1]["default"] == "1"


def test_hybrid_key_prompts_label_which_provider_does_what(wizard, capsys):
    """Hybrid asks for two keys, so each prompt has to say what it is for."""
    wizard["run"](["3", "gsk_groq", "gem_gemini"])
    out = capsys.readouterr().out
    assert "keyword extraction" in out
    assert "resume planning" in out
