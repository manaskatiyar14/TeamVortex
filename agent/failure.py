"""Structured failure parsing — deterministic evidence for recovery."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FailureSummary:
    exit_code: int = 1
    failing_tests: List[str] = field(default_factory=list)
    assertion_messages: List[str] = field(default_factory=list)
    traceback_files: List[str] = field(default_factory=list)
    line_numbers: List[int] = field(default_factory=list)
    expected: Optional[str] = None
    actual: Optional[str] = None
    error_kinds: List[str] = field(default_factory=list)
    timed_out: bool = False
    raw_excerpt: str = ""

    def to_prompt_block(self, max_chars: int = 2500) -> str:
        lines = [
            f"exit_code: {self.exit_code}",
            f"timed_out: {self.timed_out}",
            f"error_kinds: {self.error_kinds or []}",
            f"failing_tests: {self.failing_tests or []}",
            f"assertion_messages: {self.assertion_messages[:5]}",
            f"traceback_files: {self.traceback_files}",
            f"line_numbers: {self.line_numbers[:10]}",
            f"expected: {self.expected!r}",
            f"actual: {self.actual!r}",
            "raw_excerpt:",
            self.raw_excerpt[:1200],
        ]
        text = "\n".join(lines)
        if len(text) > max_chars:
            return text[:max_chars] + "\n... (truncated)"
        return text

    def fingerprint(self) -> str:
        key = "|".join(
            [
                str(self.exit_code),
                ",".join(self.failing_tests[:5]),
                ",".join(self.assertion_messages[:2]),
                ",".join(self.traceback_files[:5]),
                str(self.expected),
                str(self.actual),
                "timeout" if self.timed_out else "",
            ]
        )
        return key[:500]


def parse_failure_output(
    *,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    command: str = "",
) -> FailureSummary:
    """Extract actionable signals from test/command failure output."""
    text = f"{stdout or ''}\n{stderr or ''}"
    summary = FailureSummary(exit_code=exit_code, raw_excerpt=text[-2000:])

    if exit_code == 124 or "timed out" in text.lower() or "timeout" in (command or "").lower():
        if "timed out" in text.lower() or exit_code == 124:
            summary.timed_out = True
            summary.error_kinds.append("timeout")

    # Pytest / unittest style
    for m in re.finditer(r"FAILED\s+([\w./:\\-]+(?:::[\w]+)?)", text):
        summary.failing_tests.append(m.group(1))
    for m in re.finditer(r"^(?:FAIL|ERROR):\s+(\S+)", text, re.M):
        summary.failing_tests.append(m.group(1))
    for m in re.finditer(r"\b([\w./-]+\.py)::([\w]+)", text):
        summary.failing_tests.append(f"{m.group(1)}::{m.group(2)}")

    # Assertion / expect messages
    for m in re.finditer(r"AssertionError:?\s*(.*)", text):
        msg = m.group(1).strip()
        if msg:
            summary.assertion_messages.append(msg[:300])
            summary.error_kinds.append("assertion")
    for m in re.finditer(r"assert\s+(.+)", text):
        summary.assertion_messages.append(m.group(0)[:300])

    # Expected vs actual
    m = re.search(r"[Ee]xpected[:\s]+(.+?),\s*[Gg]ot[:\s]+(.+)", text)
    if m:
        summary.expected = m.group(1).strip()[:200]
        summary.actual = m.group(2).strip()[:200]
    m = re.search(r"assert\s+(.+?)\s*==\s*(.+)", text)
    if m and summary.expected is None:
        summary.actual = m.group(1).strip()[:200]
        summary.expected = m.group(2).strip()[:200]

    # Traceback paths / lines
    for m in re.finditer(r'File "([^"]+)", line (\d+)', text):
        path = m.group(1)
        # Prefer relative-looking tails
        rel = path.replace("\\", "/")
        if "/" in rel:
            rel = "/".join(rel.split("/")[-3:])
        summary.traceback_files.append(rel)
        summary.line_numbers.append(int(m.group(2)))
    for m in re.finditer(r"([\w./-]+\.py):(\d+)", text):
        summary.traceback_files.append(m.group(1))
        summary.line_numbers.append(int(m.group(2)))

    # Syntax / compiler
    if re.search(r"SyntaxError|IndentationError", text):
        summary.error_kinds.append("syntax")
    if re.search(r"ModuleNotFoundError|ImportError", text):
        summary.error_kinds.append("import")
    if re.search(r"TypeError|NameError|AttributeError|KeyError|ValueError", text):
        summary.error_kinds.append("runtime")
    if re.search(r"E\s+Compilation|javac:|error CS", text):
        summary.error_kinds.append("compile")

    # Deduplicate lists
    summary.failing_tests = _uniq(summary.failing_tests)[:15]
    summary.assertion_messages = _uniq(summary.assertion_messages)[:10]
    summary.traceback_files = _uniq(summary.traceback_files)[:15]
    summary.line_numbers = _uniq(summary.line_numbers)[:15]
    summary.error_kinds = _uniq(summary.error_kinds)
    return summary


def detect_no_progress(fingerprints: List[str], *, threshold: int = 2) -> bool:
    """True when the same failure fingerprint repeats enough times."""
    if len(fingerprints) < threshold:
        return False
    last = fingerprints[-1]
    streak = 0
    for fp in reversed(fingerprints):
        if fp == last:
            streak += 1
        else:
            break
    return streak >= threshold


def failing_test_progress(previous_count: int, current_count: int) -> str:
    """Classify whether the failing-test count improved, worsened, or stalled."""
    if previous_count < 0 or current_count < 0:
        return "unknown"
    if current_count < previous_count:
        return "improved"
    if current_count > previous_count:
        return "worsened"
    return "unchanged"


def count_failing_tests(text: str) -> int:
    """Best-effort failing-test count from pytest/jest/go output. -1 if unknown."""
    blob = text or ""
    m = re.search(r"(\d+)\s+failed", blob)
    if m:
        return int(m.group(1))
    m = re.search(r"Tests:\s+(\d+)\s+failed", blob)
    if m:
        return int(m.group(1))
    m = re.search(r"^FAIL\s", blob, re.M)
    if m:
        return max(1, len(re.findall(r"^FAIL\s", blob, re.M)))
    if re.search(r"(\d+)\s+passed", blob) and not re.search(r"failed", blob, re.I):
        return 0
    summary = parse_failure_output(exit_code=1, stdout=blob, stderr="")
    if summary.failing_tests:
        return len(summary.failing_tests)
    return -1


def _uniq(items: List[Any]) -> List[Any]:
    seen = set()
    out = []
    for item in items:
        key = item if not isinstance(item, list) else tuple(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
