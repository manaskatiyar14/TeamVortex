"""Path and command safety helpers for workspace-scoped execution."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import List, Tuple

# Patterns that are clearly dangerous when targeting locations outside the workspace.
_BLOCKED_PATTERNS = [
    re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?/\s*$"),
    re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)\s+/"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;"),  # fork bomb
    re.compile(r"\bshutdown\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bcurl\b.*\|\s*(ba)?sh"),
    re.compile(r"\bwget\b.*\|\s*(ba)?sh"),
    re.compile(r"\bprintenv\b"),
    re.compile(r"(?:^|\s)env(?:\s|$)"),
    re.compile(r"id_rsa"),
    re.compile(r"\$(?:\{)?(?:ai_api_key|groq_api_key|openai_api_key|anthropic_api_key)(?:\})?"),
]


def resolve_under_root(root: Path, path: str | Path) -> Path:
    """Resolve path under root; raise PermissionError on traversal."""
    root = root.resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"Path is outside the target workspace: {resolved}") from exc
    return resolved


def is_command_blocked(cmd: str, workspace: Path) -> Tuple[bool, str]:
    """Return (blocked, reason) for obviously dangerous commands.

    Commands still execute inside the workspace via subprocess cwd; this is a
    best-effort block list, not a full sandbox. Docker can be layered later.
    """
    text = (cmd or "").strip()
    if not text:
        return True, "Empty command."

    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()

    if not parts:
        return True, "Empty command."

    binary = parts[0]
    if "sudo" in parts or binary == "sudo":
        return True, "sudo is not allowed."

    lower = text.lower()
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(lower):
            return True, f"Blocked dangerous command pattern: {pattern.pattern}"

    if binary == "git" or parts[0].endswith("/git"):
        sub = next((p for p in parts[1:] if not p.startswith("-")), "")
        allowed_git = {
            "status",
            "diff",
            "log",
            "rev-parse",
            "show",
            "ls-files",
            "describe",
            "rev-list",
            "shortlog",
        }
        if sub and sub not in allowed_git:
            return True, (
                f"Git command {sub!r} is not allowed. "
                "Vortex may inspect the repository (status/diff/log) but must not "
                "commit, push, reset, or otherwise rewrite history."
            )

    if binary in {"chmod", "chown", "chgrp"}:
        return True, f"{binary} is not allowed."

    if binary in {"env", "printenv"}:
        return True, "Refusing to dump process environment (may contain credentials)."


    if re.search(r"(?:^|[\s'\"])(?:~|/)?\.ssh(?:/|\b)", text) or "/.ssh/" in text:
        return True, "Refusing access to SSH key material."

    if re.search(r"(?:^|[\s'\"])~/?\.gnupg\b", text) or "/.gnupg/" in text:
        return True, "Refusing access to GPG material."

    # Block absolute rm/mv targeting outside workspace when we can parse args.
    if binary in {"rm", "rmdir", "unlink"}:
        destructive = bool(re.search(r"-[a-zA-Z]*r[a-zA-Z]*f|[a-zA-Z]*f[a-zA-Z]*r", text))
        for arg in parts[1:]:
            if arg.startswith("-"):
                continue
            if arg in {".", "*", "..", "../", "./*"}:
                return True, f"Refusing destructive delete of {arg!r}."
            if ".." in Path(arg).parts:
                return True, f"Refusing path traversal in delete argument: {arg}"
            target = Path(arg)
            if target.is_absolute():
                try:
                    target.resolve().relative_to(workspace.resolve())
                except ValueError:
                    return True, f"Refusing to modify path outside workspace: {arg}"
        if destructive and not any(not a.startswith("-") for a in parts[1:]):
            return True, "Refusing unbounded recursive delete."

    return False, ""


def truncate_output(text: str, max_chars: int) -> str:
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n... (truncated) ...\n" + text[-half:]


def looks_like_secret(text: str) -> List[str]:
    """Heuristic scan for credentials accidentally introduced in diffs."""
    findings: List[str] = []
    patterns = [
        (r"(?i)api[_-]?key\s*=\s*['\"][^'\"]{8,}['\"]", "possible API key assignment"),
        (r"(?i)secret\s*=\s*['\"][^'\"]{8,}['\"]", "possible secret assignment"),
        (r"(?i)password\s*=\s*['\"][^'\"]{4,}['\"]", "possible password assignment"),
        (r"-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----", "private key material"),
        (r"sk-[A-Za-z0-9]{20,}", "possible OpenAI-style secret"),
        (r"gsk_[A-Za-z0-9]{20,}", "possible Groq-style secret"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, text):
            findings.append(label)
    return findings


def redact_secrets(text: str) -> str:
    """Remove live credential values from captured output. Never logs the secret."""
    if not text:
        return ""
    key = os.environ.get("AI_API_KEY", "").strip()
    if key and key in text:
        text = text.replace(key, "[REDACTED]")
    text = re.sub(r"gsk_[A-Za-z0-9]{8,}", "[REDACTED]", text)
    text = re.sub(r"sk-[A-Za-z0-9]{16,}", "[REDACTED]", text)
    return text
