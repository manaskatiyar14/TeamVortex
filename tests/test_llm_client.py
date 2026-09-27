"""LLM client guards: missing API key must fail clearly, not with a raw traceback."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from groq import BadRequestError

import config
from agent.llm_client import LLMClientError, MissingAPIKeyError, generate_text


def test_missing_api_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError, match="AI_API_KEY is not set"):
        generate_text("hello")


def _tool_use_failed_error() -> BadRequestError:
    body = {
        "error": {
            "message": "Tool choice is none, but model called a tool",
            "type": "invalid_request_error",
            "code": "tool_use_failed",
            "failed_generation": (
                '{"name": "repo_browser.open_file", '
                '"arguments": {"path":"example.py","line_start":1,"line_end":400}}'
            ),
        }
    }
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(400, request=request, json=body)
    return BadRequestError("Error code: 400", response=response, body=body)


def _patch_groq(monkeypatch, create_fn):
    class FakeCompletions:
        def create(self, **kwargs):
            return create_fn(kwargs)

    class FakeGroq:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("groq.Groq", FakeGroq)


def test_groq_retries_when_model_calls_tool(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "test-key")
    calls = {"n": 0}

    def create(kwargs):
        calls["n"] += 1
        assert kwargs["response_format"] == {"type": "json_object"}
        system = kwargs["messages"][0]["content"]
        assert "no tools" in system.lower()
        if calls["n"] == 1:
            raise _tool_use_failed_error()
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"files": ["example.py"]}', tool_calls=None)
                )
            ]
        )

    _patch_groq(monkeypatch, create)
    text = generate_text("Return JSON listing relevant files.", json_mode=True)
    assert text == '{"files": ["example.py"]}'
    assert calls["n"] == 2


def test_unsupported_provider_is_explicit(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_PROVIDER", "not-a-real-provider")
    with pytest.raises(LLMClientError, match="Unsupported LLM_PROVIDER"):
        generate_text("hello")


def test_groq_tool_use_failed_after_retries_is_llm_error(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "test-key")

    def create(_kwargs):
        raise _tool_use_failed_error()

    _patch_groq(monkeypatch, create)
    with pytest.raises(LLMClientError, match="Groq request failed"):
        generate_text("Return JSON listing relevant files.", json_mode=True)
