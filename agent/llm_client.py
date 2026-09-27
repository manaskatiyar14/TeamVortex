"""Text-only LLM client. Provider and model come from config.py only."""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from dotenv import load_dotenv

import config

load_dotenv()

_NO_TOOLS_INSTRUCTION = (
    "You are a text-only coding assistant. You have no tools, functions, APIs, "
    "or browsers. Never emit a tool call or function call of any kind "
    "(including repo_browser, browser_search, python, or code_interpreter). "
    "Never use a name/arguments JSON object as your whole reply. "
    "Answer using plain text only."
)


class MissingAPIKeyError(RuntimeError):
    """Raised when AI_API_KEY is missing so callers can exit without a traceback."""


class LLMClientError(RuntimeError):
    """Raised when the configured provider cannot complete a text request."""


def _sanitize_msg(msg: Any, api_key: str = "") -> str:
    s = str(msg)
    if api_key and api_key in s:
        s = s.replace(api_key, "[REDACTED]")
    env_key = os.environ.get("AI_API_KEY", "").strip()
    if env_key and env_key in s:
        s = s.replace(env_key, "[REDACTED]")
    return s


def ensure_api_key() -> str:
    """Return AI_API_KEY or raise MissingAPIKeyError (no stack trace at the CLI)."""
    return _require_api_key()


def _require_api_key() -> str:
    key = os.environ.get("AI_API_KEY", "").strip()
    if not key:
        raise MissingAPIKeyError(
            "AI_API_KEY is not set. Export AI_API_KEY before running VORTEX."
        )
    return key


def generate_text(
    prompt: str,
    *,
    system_instruction: Optional[str] = None,
    json_mode: bool = False,
) -> str:
    """Send a text prompt to the configured model and return text.

    This path is text-only: no image, audio, video, or multimodal inputs.
    Native model tool/function calling is disabled for these requests.
    """
    api_key = _require_api_key()
    provider = config.LLM_PROVIDER.lower().strip()

    if provider == "groq":
        return _generate_groq(
            api_key,
            prompt,
            system_instruction,
            json_mode=json_mode,
        )

    if provider in {"openai", "openai_compatible", "openai-compatible"}:
        return _generate_openai_compatible(
            api_key,
            prompt,
            system_instruction,
            json_mode=json_mode,
        )

    raise LLMClientError(
        f"Unsupported LLM_PROVIDER={config.LLM_PROVIDER!r}. "
        "Set LLM_PROVIDER in config.py (supported: 'groq', 'openai_compatible'). "
        "Do not silently substitute another provider."
    )


def _system_content(system_instruction: Optional[str], json_mode: bool) -> str:
    parts = [_NO_TOOLS_INSTRUCTION]
    if json_mode:
        parts.append("Respond with a single JSON object only. No markdown fences.")
    if system_instruction:
        parts.append(system_instruction)
    return "\n".join(parts)


def _is_tool_use_failed(exc: BaseException) -> bool:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("code") == "tool_use_failed":
            return True
    message = str(exc).lower()
    return "tool choice is none" in message or "tool_use_failed" in message


def _is_transient(exc: BaseException) -> bool:
    message = str(exc).lower()
    transient_markers = (
        "rate limit",
        "429",
        "timeout",
        "timed out",
        "503",
        "502",
        "500",
        "temporarily unavailable",
        "connection",
        "overloaded",
    )
    return any(m in message for m in transient_markers)


def _retry_user_message() -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            "That response was invalid: you must not call tools. "
            "You have no tools. Reply again with the requested text or JSON only."
        ),
    }


def _generate_groq(
    api_key: str,
    prompt: str,
    system_instruction: Optional[str],
    *,
    json_mode: bool = False,
) -> str:
    from groq import APIError, Groq

    client = Groq(api_key=api_key)
    max_attempts = max(1, int(getattr(config, "LLM_MAX_RETRIES", 3)))
    backoff = float(getattr(config, "LLM_RETRY_BACKOFF_SECONDS", 1.5))

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_content(system_instruction, json_mode)},
        {"role": "user", "content": prompt},
    ]

    last_error: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        create_kwargs: dict[str, Any] = {
            "model": config.MODEL_NAME,
            "messages": messages,
            "temperature": config.TEMPERATURE,
            "max_completion_tokens": 4096,
            "reasoning_effort": "low",
        }
        if json_mode:
            create_kwargs["response_format"] = {"type": "json_object"}

        try:
            response = client.chat.completions.create(**create_kwargs)
        except APIError as exc:
            last_error = exc
            if _is_tool_use_failed(exc) and attempt < max_attempts:
                print(
                    f"[llm] Groq rejected a tool call on a text-only request "
                    f"(attempt {attempt}/{max_attempts}); retrying."
                )
                messages.append(_retry_user_message())
                continue
            if _is_transient(exc) and attempt < max_attempts:
                sleep_for = backoff * attempt
                print(
                    f"[llm] Transient Groq error (attempt {attempt}/{max_attempts}); "
                    f"backing off {sleep_for:.1f}s: {_sanitize_msg(exc, api_key)}"
                )
                time.sleep(sleep_for)
                continue
            raise LLMClientError(f"Groq request failed: {_sanitize_msg(exc, api_key)}") from None
        except Exception as exc:  # network / timeout wrappers
            last_error = exc
            if _is_transient(exc) and attempt < max_attempts:
                sleep_for = backoff * attempt
                print(
                    f"[llm] Transient error (attempt {attempt}/{max_attempts}); "
                    f"backing off {sleep_for:.1f}s: {_sanitize_msg(exc, api_key)}"
                )
                time.sleep(sleep_for)
                continue
            raise LLMClientError(f"Groq request failed: {_sanitize_msg(exc, api_key)}") from None

        message = response.choices[0].message
        text = (message.content or "").strip()
        tool_calls = getattr(message, "tool_calls", None) or ()
        if text:
            return text
        if tool_calls and attempt < max_attempts:
            print(
                f"[llm] model returned a tool call instead of text "
                f"(attempt {attempt}/{max_attempts}); retrying."
            )
            messages.append(_retry_user_message())
            continue
        last_error = LLMClientError("Empty response from Groq model.")
        if attempt < max_attempts:
            messages.append(_retry_user_message())
            continue
        raise last_error

    raise LLMClientError(f"Groq request failed after retries: {last_error}")


def _generate_openai_compatible(
    api_key: str,
    prompt: str,
    system_instruction: Optional[str],
    *,
    json_mode: bool = False,
) -> str:
    """Text-only Chat Completions adapter for OpenAI-compatible HTTP APIs.

    Configure LLM_BASE_URL and MODEL_NAME in config.py. Uses AI_API_KEY as Bearer token.
    """
    import json
    import urllib.error
    import urllib.request

    base = str(getattr(config, "LLM_BASE_URL", "") or "https://api.openai.com/v1").rstrip("/")
    url = f"{base}/chat/completions"
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_content(system_instruction, json_mode)},
        {"role": "user", "content": prompt},
    ]
    payload: dict[str, Any] = {
        "model": config.MODEL_NAME,
        "messages": messages,
        "temperature": config.TEMPERATURE,
        "max_tokens": 4096,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    max_attempts = max(1, int(getattr(config, "LLM_MAX_RETRIES", 3)))
    backoff = float(getattr(config, "LLM_RETRY_BACKOFF_SECONDS", 1.5))
    last_error: Optional[BaseException] = None

    for attempt in range(1, max_attempts + 1):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            choices = data.get("choices") or []
            if not choices:
                raise LLMClientError("Empty choices from OpenAI-compatible API.")
            message = choices[0].get("message") or {}
            text = str(message.get("content") or "").strip()
            if text:
                return text
            last_error = LLMClientError("Empty response from OpenAI-compatible model.")
        except urllib.error.HTTPError as exc:
            last_error = exc
            detail = ""
            try:
                raw_detail = exc.read().decode("utf-8", errors="replace")[:400]
                detail = _sanitize_msg(raw_detail, api_key)
            except Exception:
                detail = _sanitize_msg(exc, api_key)
            if _is_transient(exc) and attempt < max_attempts:
                time.sleep(backoff * attempt)
                continue
            raise LLMClientError(
                f"OpenAI-compatible request failed (HTTP {exc.code}): {detail}"
            ) from None
        except Exception as exc:
            last_error = exc
            if _is_transient(exc) and attempt < max_attempts:
                time.sleep(backoff * attempt)
                continue
            raise LLMClientError(f"OpenAI-compatible request failed: {_sanitize_msg(exc, api_key)}") from None
        if attempt < max_attempts:
            time.sleep(backoff * attempt)
            continue
        raise last_error  # type: ignore[misc]

    raise LLMClientError(f"OpenAI-compatible request failed after retries: {last_error}")
