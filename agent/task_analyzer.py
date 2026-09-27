"""Task analyzer: classify repository vs standalone coding problems."""

from __future__ import annotations

import re
from typing import List, Optional

from agent.context import TaskType

ISSUE_KINDS = (
    "BUG_FIX",
    "FEATURE",
    "TEST_FAILURE",
    "REFACTOR",
    "CONFIGURATION",
    "DEPENDENCY",
    "PERFORMANCE",
    "DOCUMENTATION",
    "STANDALONE",
    "UNKNOWN",
)


_STANDALONE_HINTS = [
    r"\bleetcode\b",
    r"\bhackerrank\b",
    r"\bcoding\s+problem\b",
    r"\balgorithmic\b",
    r"\bwrite\s+a\s+function\b",
    r"\bimplement\s+a\s+function\b",
    r"\bstdin\b",
    r"\bstdout\b",
    r"\btime\s+complexity\b",
    r"\bspace\s+complexity\b",
    r"\bexample\s*\d*\s*:",
    r"\bconstraints?\b",
    r"\breturn\s+the\b",
]

_REPO_HINTS = [
    r"\bfix\b",
    r"\bbug\b",
    r"\bissue\s*#?\d+",
    r"\brepository\b",
    r"\bpr\b",
    r"\bpytest\b",
    r"\bfailing\s+test",
    r"\bdelete\s+/",
    r"\bendpoint\b",
    r"\bauthentication\b",
]


def analyze_task(
    task: str,
    *,
    explicit_mode: Optional[str] = None,
    has_repo: bool = False,
) -> TaskType:
    """Classify the task. Explicit mode wins; otherwise use heuristics + presence of repo."""
    if explicit_mode:
        mode = explicit_mode.strip().lower()
        if mode in {"1", "repo", "repository", "repository_engineering"}:
            return TaskType.REPOSITORY
        if mode in {"2", "standalone", "coding", "dsa", "leetcode"}:
            return TaskType.STANDALONE

    text = (task or "").strip()
    if not text:
        return TaskType.REPOSITORY if has_repo else TaskType.STANDALONE

    standalone_score = sum(1 for p in _STANDALONE_HINTS if re.search(p, text, re.I))
    repo_score = sum(1 for p in _REPO_HINTS if re.search(p, text, re.I))

    if has_repo and repo_score >= standalone_score:
        return TaskType.REPOSITORY
    if standalone_score > repo_score and not has_repo:
        return TaskType.STANDALONE
    if has_repo:
        return TaskType.REPOSITORY
    return TaskType.STANDALONE


def summarize_task(task: str, task_type: TaskType, *, issue_kind: str = "") -> str:
    """Short human-readable analysis line (no LLM required)."""
    kind = "Repository engineering task" if task_type == TaskType.REPOSITORY else "Standalone coding problem"
    preview = " ".join(task.split())
    if len(preview) > 160:
        preview = preview[:157] + "..."
    suffix = f" [{issue_kind}]" if issue_kind and issue_kind != "UNKNOWN" else ""
    return f"{kind}{suffix}: {preview}"


def classify_issue(task: str) -> str:
    """Coarse issue kind. Returns UNKNOWN rather than forcing a label."""
    text = task or ""
    lower = text.lower()
    scores = {k: 0 for k in ISSUE_KINDS}

    def bump(kind: str, n: int = 1) -> None:
        scores[kind] += n

    if re.search(r"\bfailing\s+tests?\b|\bpytest\b|\bassert\b|\btest_\w+", text, re.I):
        bump("TEST_FAILURE", 2)
    if re.search(r"\bbug\b|\bfix\b|\berror\b|\bcrash\b|\bexception\b|\bregression\b", text, re.I):
        bump("BUG_FIX", 2)
    if re.search(r"\btraceback\b|\bstack\s*trace\b|\bTypeError\b|\bAssertionError\b", text, re.I):
        bump("BUG_FIX", 2)
    if re.search(r"\badd\s+(support|feature)|implement\b|\bnew\s+endpoint\b|\bfeature\b", text, re.I):
        bump("FEATURE", 2)
    if re.search(r"\brefactor\b|\brename\b|\bclean\s+up\b", text, re.I):
        bump("REFACTOR", 2)
    if re.search(r"\bconfig(uration)?\b|\benv(ironment)?\b|\byaml\b|\btoml\b", text, re.I):
        bump("CONFIGURATION", 1)
    if re.search(r"\bdependenc|\bpackage\.json\b|\brequirements\.txt\b|\bpip\s+install\b|\bnpm\s+install\b", text, re.I):
        bump("DEPENDENCY", 2)
    if re.search(r"\bperformance\b|\bslow\b|\blatency\b|\bmemory\b|\btimeout\b", text, re.I):
        bump("PERFORMANCE", 1)
    if re.search(r"\breadme\b|\bdocs?\b|\bdocumentation\b|\btypo\b", text, re.I):
        bump("DOCUMENTATION", 2)

    ranked = sorted(
        ((k, v) for k, v in scores.items() if k != "UNKNOWN"),
        key=lambda kv: -kv[1],
    )
    if not ranked or ranked[0][1] == 0:
        return "UNKNOWN"
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return "UNKNOWN"
    if ranked[0][1] < 2:
        return "UNKNOWN"
    return ranked[0][0]


def extract_acceptance_criteria(task: str) -> List[str]:
    """Turn explicit expected behavior into checkable notes (no LLM)."""
    text = task or ""
    criteria: List[str] = []
    for m in re.finditer(r"(?im)^(?:expected|acceptance|should|must)[:\s]+(.+)$", text):
        criteria.append(m.group(0).strip()[:240])
    for m in re.finditer(r"(?i)should\s+([^.!\n]{8,160})", text):
        item = "should " + m.group(1).strip()
        if item not in criteria:
            criteria.append(item[:240])
    quoted = re.findall(r'"([^"\n]{3,120})"', text)
    for q in quoted[:8]:
        criteria.append(f'preserve/observe: "{q}"')
    # Dedup
    out: List[str] = []
    seen = set()
    for c in criteria:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:12]


def extract_issue_signals(task: str) -> dict:
    """Extract search-friendly signals from GitHub-style issue / bug text (no network)."""
    text = task or ""
    paths = re.findall(
        r"[\w./-]+\.(?:py|js|ts|tsx|jsx|go|java|rb|rs|php|sql|json|toml|ya?ml|xml)\b",
        text,
    )
    symbols = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b", text)
    stop = {
        "the", "and", "for", "that", "with", "this", "from", "failing", "tests", "test",
        "fix", "bug", "issue", "implement", "feature", "function", "return", "should",
        "must", "when", "then", "into", "about", "expected", "actual", "behavior",
        "steps", "reproduce", "error", "failed", "please", "would", "could", "have",
        "been", "will", "does", "file", "code", "line", "stack", "trace",
    }
    keywords = []
    for s in symbols:
        if s.lower() in stop:
            continue
        if s not in keywords:
            keywords.append(s)
    stack_files = re.findall(r'File "([^"]+)"', text)
    stack_files += re.findall(r"([\w./-]+\.py):(\d+)", text)
    # Normalize stack_files to path strings
    stack_paths = []
    for item in stack_files:
        if isinstance(item, tuple):
            stack_paths.append(item[0])
        else:
            stack_paths.append(item)

    expected = None
    actual = None
    m = re.search(r"[Ee]xpected[:\s]+(.+?)(?:\n|$)", text)
    if m:
        expected = m.group(1).strip()[:200]
    m = re.search(r"[Aa]ctual[:\s]+(.+?)(?:\n|$)", text)
    if m:
        actual = m.group(1).strip()[:200]

    quoted = re.findall(r'"([^"\n]{3,160})"', text)
    quoted += re.findall(r"'([^'\n]{3,160})'", text)
    error_lines = re.findall(r"(?m)^(E\s+.+|[A-Za-z]*Error: .+)$", text)

    return {
        "paths": list(dict.fromkeys(paths))[:20],
        "keywords": keywords[:25],
        "symbols": [k for k in keywords if k[:1].isupper() or "_" in k][:20],
        "stack_paths": list(dict.fromkeys(stack_paths))[:15],
        "expected": expected,
        "actual": actual,
        "has_stack_trace": bool(stack_paths) or "traceback" in text.lower(),
        "quoted": list(dict.fromkeys(quoted))[:15],
        "error_lines": list(dict.fromkeys(error_lines))[:10],
        "issue_kind": classify_issue(text),
        "acceptance_criteria": extract_acceptance_criteria(text),
    }
