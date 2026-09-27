"""Lightweight repository map — structure without dumping the whole tree to the model."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set

import config
from agent import tools

_SOURCE_ROOT_HINTS = ("src", "lib", "app", "pkg", "packages", "server", "backend", "frontend")
_TEST_ROOT_HINTS = ("tests", "test", "__tests__", "spec")
_ENTRY_HINTS = (
    "main.py",
    "app.py",
    "manage.py",
    "index.js",
    "index.ts",
    "server.js",
    "main.go",
    "cmd/",
)
_LAYER_PATTERNS = {
    "routes": re.compile(r"(route|router|routes|endpoint|urls)", re.I),
    "controllers": re.compile(r"(controller|handlers?|views?)", re.I),
    "services": re.compile(r"(service|services)", re.I),
    "models": re.compile(r"(model|models|schema|entity)", re.I),
    "utilities": re.compile(r"(util|utils|helper|helpers|common)", re.I),
}


def build_repo_map(
    file_listing: List[str] | None = None,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a compact structural map of the current workspace."""
    root = tools.get_repo_root()
    files = file_listing if file_listing is not None else tools.list_files()
    metadata = metadata if metadata is not None else tools.detect_project_metadata()

    languages = _detect_languages(files)
    source_roots = _detect_roots(files, _SOURCE_ROOT_HINTS)
    test_roots = _detect_roots(files, _TEST_ROOT_HINTS)
    entry_points = [f for f in files if any(h in f for h in _ENTRY_HINTS)][:20]
    layers = _classify_layers(files)
    test_files = [f for f in files if _is_test_file(f)]
    config_files = list((metadata.get("files") or {}).values())

    symbols: List[Dict[str, Any]] = []
    # Cheap structural map always. Symbol skim only on modest repositories —
    # large trees are searched on demand instead of ingesting source wholesale.
    threshold = int(getattr(config, "LARGE_REPO_FILE_THRESHOLD", 200))
    max_skim = int(getattr(config, "MAX_SYMBOL_SKIM_FILES", 40))
    if len(files) <= threshold:
        candidates = [
            f
            for f in files
            if f.endswith((".py", ".js", ".ts", ".go", ".java")) and not _is_test_file(f)
        ][:max_skim]
        for path in candidates:
            try:
                content = tools.read_file(path)
            except (OSError, PermissionError, UnicodeError):
                continue
            for sym in _extract_symbols(path, content):
                symbols.append(sym)
                if len(symbols) >= 80:
                    break
            if len(symbols) >= 80:
                break

    repo_map = {
        "root": str(root),
        "project_type": metadata.get("project_type", "unknown"),
        "languages": languages,
        "source_roots": source_roots,
        "test_roots": test_roots,
        "config_files": config_files,
        "entry_points": entry_points,
        "layers": {k: v[:15] for k, v in layers.items()},
        "test_files": test_files[:40],
        "file_count": len(files),
        "symbols_sample": symbols[:80],
    }
    print(
        f"[repo_map] type={repo_map['project_type']} files={len(files)} "
        f"langs={languages} tests={len(test_files)}"
    )
    return repo_map


def repo_map_prompt_block(repo_map: Dict[str, Any], max_chars: int = 2500) -> str:
    """Compact text for LLM prompts."""
    lines = [
        f"project_type: {repo_map.get('project_type')}",
        f"languages: {repo_map.get('languages')}",
        f"source_roots: {repo_map.get('source_roots')}",
        f"test_roots: {repo_map.get('test_roots')}",
        f"config_files: {repo_map.get('config_files')}",
        f"entry_points: {repo_map.get('entry_points')}",
        f"file_count: {repo_map.get('file_count')}",
    ]
    layers = repo_map.get("layers") or {}
    for name, paths in layers.items():
        if paths:
            lines.append(f"{name}: {paths}")
    tests = repo_map.get("test_files") or []
    if tests:
        lines.append(f"test_files (sample): {tests[:15]}")
    symbols = repo_map.get("symbols_sample") or []
    if symbols:
        sym_bits = [f"{s.get('kind')} {s.get('name')}@{s.get('path')}" for s in symbols[:25]]
        lines.append("symbols (sample): " + "; ".join(sym_bits))
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[:max_chars] + "\n... (truncated)"
    return text


def find_filename_matches(query: str, files: List[str]) -> List[str]:
    q = query.lower()
    return [f for f in files if q in f.lower()][:20]


def extract_imports(content: str, language_hint: str = "") -> List[str]:
    """Best-effort import/require extraction."""
    imports: List[str] = []
    patterns = [
        re.compile(r"^\s*from\s+([\w.]+)\s+import\s+", re.M),
        re.compile(r"^\s*import\s+([\w.]+)", re.M),
        re.compile(r"""require\(\s*['\"]([^'\"]+)['\"]\s*\)"""),
        re.compile(r"""from\s+['\"]([^'\"]+)['\"]"""),
        re.compile(r"""import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]"""),
    ]
    for pat in patterns:
        for m in pat.finditer(content or ""):
            imports.append(m.group(1))
    # Deduplicate preserving order
    seen: Set[str] = set()
    out: List[str] = []
    for item in imports:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out[:40]


def resolve_import_to_files(import_name: str, files: List[str]) -> List[str]:
    """Map a Python/Node-ish import to candidate relative paths."""
    token = import_name.replace(".", "/").lstrip("./")
    candidates = []
    for f in files:
        stem = Path(f).with_suffix("").as_posix()
        if stem.endswith(token) or token in stem or Path(f).stem == Path(token).name:
            candidates.append(f)
    return candidates[:10]


def _detect_languages(files: List[str]) -> List[str]:
    counts: Dict[str, int] = defaultdict(int)
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".go": "go",
        ".java": "java",
        ".rs": "rust",
        ".rb": "ruby",
        ".sql": "sql",
    }
    for f in files:
        ext = Path(f).suffix.lower()
        if ext in mapping:
            counts[mapping[ext]] += 1
    return [lang for lang, _ in sorted(counts.items(), key=lambda x: -x[1])]


def _detect_roots(files: List[str], hints: tuple) -> List[str]:
    roots: Set[str] = set()
    for f in files:
        parts = Path(f).parts
        if parts and parts[0] in hints:
            roots.add(parts[0])
    return sorted(roots)


def _classify_layers(files: List[str]) -> Dict[str, List[str]]:
    layers: Dict[str, List[str]] = {k: [] for k in _LAYER_PATTERNS}
    for f in files:
        for name, pat in _LAYER_PATTERNS.items():
            if pat.search(f):
                layers[name].append(f)
    return layers


def _is_test_file(path: str) -> bool:
    p = path.lower()
    name = Path(p).name
    return (
        "/test" in p
        or p.startswith("test")
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("_test.go")
        or ".test." in name
        or name.endswith("_spec.js")
    )


def _extract_symbols(path: str, content: str) -> List[Dict[str, Any]]:
    symbols: List[Dict[str, Any]] = []
    patterns = [
        (re.compile(r"^\s*def\s+(\w+)\s*\(", re.M), "function"),
        (re.compile(r"^\s*class\s+(\w+)\b", re.M), "class"),
        (re.compile(r"^\s*function\s+(\w+)\s*\(", re.M), "function"),
        (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.M), "function"),
        (re.compile(r"^\s*func\s+(\w+)\s*\(", re.M), "function"),
    ]
    for pat, kind in patterns:
        for m in pat.finditer(content):
            symbols.append(
                {
                    "name": m.group(1),
                    "kind": kind,
                    "path": path,
                    "line": content[: m.start()].count("\n") + 1,
                }
            )
            if len(symbols) >= 20:
                return symbols
    return symbols
