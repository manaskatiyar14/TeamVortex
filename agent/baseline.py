"""Workspace baseline — distinguish pre-existing changes from Vortex-caused edits."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from agent import tools


NOISE_SUFFIXES = (".pyc", ".pyo", ".class", ".o", ".so")
NOISE_PARTS = (
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".DS_Store",
    "htmlcov",
    ".coverage",
    "node_modules",
    ".git",
)


@dataclass
class WorkspaceBaseline:
    """Snapshot of workspace dirtiness at task start."""

    git_available: bool = False
    status_output: str = ""
    changed_files: List[str] = field(default_factory=list)
    diff_excerpt: str = ""
    content_hashes: Dict[str, str] = field(default_factory=dict)
    vortex_writes: List[str] = field(default_factory=list)
    pre_edit_snapshots: Dict[str, str] = field(default_factory=dict)

    def record_vortex_write(self, path: str, previous_content: Optional[str] = None) -> None:
        if path not in self.vortex_writes:
            self.vortex_writes.append(path)
        if previous_content is not None and path not in self.pre_edit_snapshots:
            self.pre_edit_snapshots[path] = previous_content

    def to_dict(self) -> Dict[str, Any]:
        return {
            "git_available": self.git_available,
            "changed_files_at_start": list(self.changed_files),
            "vortex_writes": list(self.vortex_writes),
        }


def capture_baseline() -> WorkspaceBaseline:
    """Capture git status/diff and optional content hashes at task start."""
    baseline = WorkspaceBaseline()
    status = tools.git_status()
    diff = tools.git_diff()
    baseline.git_available = bool(status.get("available"))
    if baseline.git_available:
        baseline.status_output = str(status.get("output") or "")
        baseline.changed_files = [
            p for p in (status.get("changed_files") or []) if not is_noise_path(p)
        ]
        baseline.diff_excerpt = str(diff.get("diff") or "")[:4000]
    print(
        f"[baseline] git={baseline.git_available} "
        f"pre_existing={len(baseline.changed_files)} file(s)"
    )
    return baseline


def hash_content(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8", errors="replace")).hexdigest()[:16]


def is_noise_path(path: str) -> bool:
    p = path.replace("\\", "/")
    lower = p.lower()
    if any(part in lower for part in NOISE_PARTS):
        return True
    return any(lower.endswith(suf) for suf in NOISE_SUFFIXES)


def classify_changes(
    baseline: WorkspaceBaseline,
    *,
    current_changed: List[str],
    vortex_modified: List[str],
) -> Dict[str, List[str]]:
    """Split current dirty files into pre-existing vs Vortex-caused vs unknown."""
    pre: Set[str] = set(baseline.changed_files)
    vortex: Set[str] = set(baseline.vortex_writes) | set(vortex_modified)
    current = [p for p in current_changed if not is_noise_path(p)]

    pre_existing = []
    caused_by_vortex = []
    other = []
    for path in current:
        if path in vortex:
            caused_by_vortex.append(path)
        elif path in pre:
            pre_existing.append(path)
        else:
            # New dirt after start and not recorded as Vortex write — treat carefully
            other.append(path)

    # Also include vortex writes that may not show in git yet (new files always do)
    for path in vortex:
        if path not in caused_by_vortex and not is_noise_path(path):
            caused_by_vortex.append(path)

    return {
        "pre_existing": pre_existing,
        "caused_by_vortex": caused_by_vortex,
        "other": other,
        "noise_ignored": [p for p in current_changed if is_noise_path(p)],
    }
