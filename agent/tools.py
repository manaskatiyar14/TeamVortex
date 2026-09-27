"""Repository tools: list, read, write, edit, search, shell, tests, git."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from agent.safety import (
    is_command_blocked,
    redact_secrets,
    resolve_under_root,
    truncate_output,
)

_file_cache: Dict[str, str] = {}
_repo_root: Optional[Path] = None


def set_repo_root(path: str | Path) -> Path:
    """Bind tools to a workspace / repository provided at runtime."""
    global _repo_root
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Repository path does not exist or is not a directory: {root}")
    _repo_root = root
    print(f"[tool] set_repo_root({root})")
    return root


def get_repo_root() -> Path:
    if _repo_root is None:
        raise RuntimeError("Repository root is not set. Pass a repo path when starting the agent.")
    return _repo_root


def clear_file_cache() -> None:
    """Clear the per-task read cache (call at the start of a new task)."""
    _file_cache.clear()
    print("[tool] clear_file_cache()")


def _resolve_under_repo(path: str | Path) -> Path:
    return resolve_under_root(get_repo_root(), path)


def invalidate_cache(path: str | Path | None = None) -> None:
    if path is None:
        _file_cache.clear()
        return
    resolved = _resolve_under_repo(path)
    _file_cache.pop(str(resolved), None)


# ---------------------------------------------------------------------------
# Listing / metadata
# ---------------------------------------------------------------------------


def list_files(directory: str | Path | None = None) -> List[str]:
    """Recursively list files under directory (defaults to the repo root)."""
    root = get_repo_root()
    base = _resolve_under_repo(directory) if directory is not None else root
    print(f"[tool] list_files({base})")

    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in config.SKIP_DIR_NAMES]
        for name in filenames:
            full = Path(dirpath) / name
            # Skip huge binaries / generated artifacts by extension heuristic
            if full.suffix.lower() in {".pyc", ".pyo", ".so", ".o", ".a", ".class", ".jar"}:
                continue
            try:
                if full.stat().st_size > 2_000_000:
                    continue
            except OSError:
                continue
            rel = full.relative_to(root).as_posix()
            files.append(rel)
    files.sort()
    print(f"[tool] list_files -> {len(files)} file(s)")
    return files


def detect_project_metadata() -> Dict[str, Any]:
    """Locate important repository metadata files and infer a coarse project type."""
    root = get_repo_root()
    found: Dict[str, str] = {}
    for name in config.METADATA_FILENAMES:
        candidate = root / name
        if candidate.is_file():
            found[name] = name

    # Also search one level deep for common metadata
    for child in root.iterdir():
        if child.is_dir() and child.name not in config.SKIP_DIR_NAMES:
            for name in ("package.json", "pyproject.toml", "go.mod", "pom.xml"):
                nested = child / name
                if nested.is_file() and name not in found:
                    found[name] = nested.relative_to(root).as_posix()

    project_type = "unknown"
    if any(k in found for k in ("pyproject.toml", "requirements.txt", "pytest.ini", "setup.py", "setup.cfg")):
        project_type = "python"
    elif "package.json" in found or "tsconfig.json" in found:
        project_type = "node"
    elif "go.mod" in found:
        project_type = "go"
    elif "pom.xml" in found:
        project_type = "java-maven"
    elif any(k in found for k in ("build.gradle", "build.gradle.kts")):
        project_type = "java-gradle"
    elif "Cargo.toml" in found:
        project_type = "rust"

    result = {"files": found, "project_type": project_type, "root": str(root)}
    print(f"[tool] detect_project_metadata -> {project_type} ({len(found)} metadata file(s))")
    return result


# ---------------------------------------------------------------------------
# Read / write / edit
# ---------------------------------------------------------------------------


def read_file(path: str | Path) -> str:
    """Read a text file. Cached for the current task so the same path is not re-read."""
    resolved = _resolve_under_repo(path)
    key = str(resolved)
    if key in _file_cache:
        print(f"[tool] read_file({resolved}) [cache hit]")
        return _file_cache[key]
    print(f"[tool] read_file({resolved})")
    try:
        size = resolved.stat().st_size
    except OSError:
        size = 0
    max_raw = int(getattr(config, "MAX_RAW_FILE_CHARS", 200_000))
    if size > max_raw * 2:
        raise UnicodeError(f"Refusing to load oversized file ({size} bytes): {path}")
    content = resolved.read_text(encoding="utf-8", errors="replace")
    if "\x00" in content[:4096]:
        raise UnicodeError(f"Refusing to load binary file: {path}")
    if len(content) > max_raw:
        content = content[:max_raw] + "\n... (truncated oversized file)"
    _file_cache[key] = content
    return content


def _invalidate_compiled_cache(resolved: Path) -> None:
    """Remove any compiled bytecode cache for a source file after it changes.

    CPython's default (non-hash-based) .pyc validation keys on the source
    file's mtime *and* size. When a rewrite happens within the same
    filesystem mtime resolution window (common for fast, back-to-back
    writes in tests/agent loops) and the new content happens to be the same
    length as the old content, both checks can spuriously match a
    previously compiled .pyc left in __pycache__ (e.g. from an earlier
    baseline test run). A freshly spawned subprocess would then silently
    execute the *old* bytecode instead of the newly written source,
    breaking the "write then read/execute sees new content" invariant.
    Proactively deleting the cached bytecode for this exact file removes
    that race entirely, regardless of mtime/size coincidences.
    """
    if resolved.suffix != ".py":
        return
    pycache_dir = resolved.parent / "__pycache__"
    if not pycache_dir.is_dir():
        return
    prefix = resolved.stem + "."
    for cached in pycache_dir.glob(f"{prefix}*.pyc"):
        try:
            cached.unlink()
        except OSError:
            pass


def write_file(path: str | Path, content: str) -> None:
    """Write text to a file and update the read cache.

    Also invalidates any stale compiled bytecode (__pycache__/*.pyc) for
    this file so that subprocesses (e.g. the verification test run) always
    observe the new content rather than a stale cached compilation. See
    _invalidate_compiled_cache for why this is necessary.
    """
    resolved = _resolve_under_repo(path)
    print(f"[tool] write_file({resolved}) [{len(content)} bytes]")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    _file_cache[str(resolved)] = content
    _invalidate_compiled_cache(resolved)


def edit_file(path: str | Path, old: str, new: str) -> Dict[str, Any]:
    """Replace the first occurrence of old with new in a file. Structured result."""
    resolved = _resolve_under_repo(path)
    print(f"[tool] edit_file({resolved})")
    content = read_file(path)
    if old not in content:
        return {"ok": False, "path": str(path), "error": "old text not found"}
    if content.count(old) > 1 and len(old) < 12:
        return {
            "ok": False,
            "path": str(path),
            "error": "old text is not unique; provide a larger unique snippet",
        }
    updated = content.replace(old, new, 1)
    write_file(path, updated)
    return {"ok": True, "path": str(path), "chars": len(updated)}


def apply_file_operations(operations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply a batch of writes/replacements transactionally.

    On any failure after validation, previously applied ops in this batch are rolled back.
    """
    if not operations:
        return {"ok": False, "error": "empty operation list", "applied": []}

    seen: Dict[str, Dict[str, Any]] = {}
    normalized: List[Dict[str, Any]] = []
    for op in operations:
        if not isinstance(op, dict):
            return {"ok": False, "error": "malformed operation", "applied": []}
        path = op.get("path")
        if not isinstance(path, str) or not path.strip():
            return {"ok": False, "error": "missing path", "applied": []}
        path = path.strip()
        if path in seen:
            return {"ok": False, "error": f"duplicate/conflicting edit for {path}", "applied": []}
        try:
            _resolve_under_repo(path)
        except PermissionError as exc:
            return {"ok": False, "error": str(exc), "applied": []}
        seen[path] = op
        normalized.append({**op, "path": path})

    snapshots: Dict[str, Optional[str]] = {}
    created: List[str] = []
    applied: List[str] = []
    fingerprint_parts: List[str] = []

    def _rollback() -> None:
        for p in created:
            try:
                full = _resolve_under_repo(p)
                if full.is_file():
                    full.unlink()
            except (OSError, PermissionError):
                continue
        for p, old in snapshots.items():
            if old is None:
                continue
            try:
                write_file(p, old)
            except (OSError, PermissionError):
                continue

    try:
        for op in normalized:
            path = op["path"]
            resolved = _resolve_under_repo(path)
            existed = resolved.is_file()
            previous = read_file(path) if existed else None
            if existed:
                snapshots[path] = previous
            else:
                created.append(path)
                snapshots[path] = None

            if "old" in op and "new" in op:
                old, new = op["old"], op["new"]
                if not existed:
                    raise RuntimeError(f"targeted edit target does not exist: {path}")
                if old == new:
                    raise RuntimeError(f"no-op targeted edit: {path}")
                if old not in (previous or ""):
                    raise RuntimeError(f"old text not found in {path}")
                updated = (previous or "").replace(old, new, 1)
            elif "content" in op:
                updated = op["content"]
                if not isinstance(updated, str):
                    raise RuntimeError(f"invalid content for {path}")
                if existed and previous == updated:
                    raise RuntimeError(f"no-op whole-file write: {path}")
                if existed and previous and len(previous) > 200 and len(updated) < max(20, int(len(previous) * 0.15)):
                    raise RuntimeError(f"refusing accidental large deletion in {path}")
            else:
                raise RuntimeError(f"unsupported operation for {path}")

            write_file(path, updated)
            applied.append(path)
            fingerprint_parts.append(f"{path}:{hash(updated)}")
    except (OSError, PermissionError, RuntimeError, UnicodeError) as exc:
        _rollback()
        return {"ok": False, "error": str(exc), "applied": []}

    return {
        "ok": True,
        "applied": applied,
        "fingerprint": "|".join(fingerprint_parts),
        "error": "",
    }


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _rg_available() -> bool:
    return shutil.which("rg") is not None


def search_code(
    query: str,
    *,
    glob: str | None = None,
    max_results: int | None = None,
) -> List[Dict[str, Any]]:
    """Search repository contents. Prefer ripgrep; fall back to Python walk."""
    root = get_repo_root()
    limit = max_results or config.MAX_SEARCH_RESULTS
    print(f"[tool] search_code({query!r}, glob={glob!r})")

    if _rg_available():
        return _search_with_rg(root, query, glob=glob, limit=limit)
    return _search_with_python(root, query, glob=glob, limit=limit)


def _search_with_rg(
    root: Path,
    query: str,
    *,
    glob: str | None,
    limit: int,
) -> List[Dict[str, Any]]:
    cmd = [
        "rg",
        "--json",
        "--max-count",
        str(limit),
        "-n",
        "--hidden",
        "--glob",
        "!.git",
        "--glob",
        "!node_modules",
        "--glob",
        "!.venv",
        "--glob",
        "!venv",
        "--glob",
        "!__pycache__",
        "--glob",
        "!dist",
        "--glob",
        "!build",
        "--glob",
        "!coverage",
    ]
    if glob:
        cmd.extend(["--glob", glob])
    cmd.extend(["--", query, str(root)])
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _search_with_python(root, query, glob=glob, limit=limit)

    results: List[Dict[str, Any]] = []
    for line in (completed.stdout or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data") or {}
        path = data.get("path", {}).get("text", "")
        try:
            rel = Path(path).resolve().relative_to(root).as_posix()
        except Exception:
            rel = path
        line_number = data.get("line_number")
        text = (data.get("lines") or {}).get("text", "").rstrip("\n")
        results.append({"path": rel, "line": line_number, "text": text[:500]})
        if len(results) >= limit:
            break
    print(f"[tool] search_code (rg) -> {len(results)} hit(s)")
    return results


def _search_with_python(
    root: Path,
    query: str,
    *,
    glob: str | None,
    limit: int,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    pattern = re.compile(re.escape(query)) if query else re.compile(".")
    suffix_filter = None
    if glob and glob.startswith("*."):
        suffix_filter = glob[1:]  # .py

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in config.SKIP_DIR_NAMES]
        for name in filenames:
            if suffix_filter and not name.endswith(suffix_filter):
                continue
            full = Path(dirpath) / name
            try:
                if full.stat().st_size > 1_000_000:
                    continue
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    rel = full.relative_to(root).as_posix()
                    results.append({"path": rel, "line": i, "text": line[:500]})
                    if len(results) >= limit:
                        print(f"[tool] search_code (python) -> {len(results)} hit(s)")
                        return results
    print(f"[tool] search_code (python) -> {len(results)} hit(s)")
    return results


def find_symbol(symbol: str, *, max_results: int | None = None) -> List[Dict[str, Any]]:
    """Best-effort symbol search (def/class/function/name occurrences)."""
    print(f"[tool] find_symbol({symbol!r})")
    # Use plain substrings so both ripgrep and the Python fallback behave.
    queries = [
        f"def {symbol}",
        f"class {symbol}",
        f"function {symbol}",
        f"{symbol} =",
        symbol,
    ]
    seen = set()
    results: List[Dict[str, Any]] = []
    limit = max_results or config.MAX_SEARCH_RESULTS
    for query in queries:
        for hit in search_code(query, max_results=limit):
            key = (hit["path"], hit["line"])
            if key in seen:
                continue
            seen.add(key)
            results.append(hit)
            if len(results) >= limit:
                return results
    return results


def find_related_tests(implementation_path: str) -> List[str]:
    """Heuristically locate tests related to an implementation file."""
    root = get_repo_root()
    print(f"[tool] find_related_tests({implementation_path!r})")
    impl = Path(implementation_path)
    stem = impl.stem
    name = impl.name
    candidates: List[str] = []

    all_files = list_files()
    test_like = [
        f
        for f in all_files
        if (
            "/test" in f.lower()
            or f.lower().startswith("test")
            or f.endswith("_test.py")
            or f.endswith("_test.go")
            or f.endswith(".test.js")
            or f.endswith(".test.ts")
            or f.endswith("_test.rb")
        )
    ]

    # Prefer name-based matches
    for f in test_like:
        base = Path(f).name.lower()
        if stem.lower() in base or name.lower() in base or f"test_{stem.lower()}" in base:
            candidates.append(f)

    # Same directory tests/
    parent = impl.parent.as_posix()
    for f in test_like:
        if f.startswith(f"{parent}/") or f.startswith("tests/") or f.startswith("test/"):
            if f not in candidates:
                candidates.append(f)

    # Content search for imports of the module
    module_token = stem
    for hit in search_code(module_token, glob="*test*", max_results=20):
        if hit["path"] not in candidates and hit["path"] in test_like:
            candidates.append(hit["path"])

    # Fallback: any test file if nothing matched
    if not candidates:
        candidates = test_like[:10]

    print(f"[tool] find_related_tests -> {candidates[:10]}")
    return candidates[:10]


# ---------------------------------------------------------------------------
# Commands / tests
# ---------------------------------------------------------------------------


def run_command(
    cmd: str,
    *,
    cwd: str | Path | None = None,
    timeout: int | None = None,
) -> Dict[str, Any]:
    """Run a shell command inside the workspace with safety checks.

    Returns structured {exit_code, stdout, stderr, blocked, command}.
    """
    root = get_repo_root()
    workdir = _resolve_under_repo(cwd) if cwd is not None else root
    blocked, reason = is_command_blocked(cmd, root)
    if blocked:
        print(f"[tool] run_command BLOCKED: {reason}")
        return {
            "exit_code": 126,
            "stdout": "",
            "stderr": reason,
            "blocked": True,
            "command": cmd,
        }

    print(f"[tool] run_command({cmd!r}, cwd={workdir})")
    env = os.environ.copy()
    for secret_name in (
        "AI_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        env.pop(secret_name, None)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    # Never let a spawned test/verification process write new compiled
    # bytecode caches. Combined with _invalidate_compiled_cache (which
    # deletes stale caches at write time), this guarantees every subprocess
    # always executes against the exact source just written, never a
    # previously cached compilation.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    timeout_s = timeout or config.COMMAND_TIMEOUT_SECONDS
    try:
        completed = subprocess.run(
            cmd,
            shell=True,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )
        stdout = redact_secrets(truncate_output(completed.stdout or "", config.MAX_COMMAND_OUTPUT_CHARS))
        stderr = redact_secrets(truncate_output(completed.stderr or "", config.MAX_COMMAND_OUTPUT_CHARS))
        print(f"[tool] run_command exit={completed.returncode}"); print(f"[tool] stdout={stdout!r}"); print(f"[tool] stderr={stderr!r}")
        return {
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "blocked": False,
            "command": cmd,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = redact_secrets(
            truncate_output((exc.stdout or "") if isinstance(exc.stdout, str) else "", config.MAX_COMMAND_OUTPUT_CHARS)
        )
        stderr = redact_secrets(
            truncate_output(
                ((exc.stderr or "") if isinstance(exc.stderr, str) else "")
                + f"\nERROR: command timed out after {timeout_s}s: {cmd}",
                config.MAX_COMMAND_OUTPUT_CHARS,
            )
        )
        print(f"[tool] run_command TIMEOUT after {timeout_s}s")
        return {
            "exit_code": 124,
            "stdout": stdout,
            "stderr": stderr,
            "blocked": False,
            "command": cmd,
            "timeout": True,
        }


def run_shell_command(cmd: str, *, cwd: str | Path | None = None) -> Tuple[int, str, str]:
    """Backward-compatible wrapper returning (code, stdout, stderr)."""
    result = run_command(cmd, cwd=cwd)
    return int(result["exit_code"]), str(result["stdout"]), str(result["stderr"])


def run_tests(
    test_path: str | Path | None = None,
    *,
    command: str | None = None,
) -> Tuple[int, str, str]:
    """Run tests. Prefer an explicit command; else pytest heuristics (legacy default)."""
    root = get_repo_root()
    if command:
        print(f"[tool] run_tests(command={command!r})")
        result = run_command(command)
        return int(result["exit_code"]), str(result["stdout"]), str(result["stderr"])

    if test_path is None:
        tests_dir = root / "tests"
        target = tests_dir if tests_dir.is_dir() else root
    else:
        target = _resolve_under_repo(test_path)
    print(f"[tool] run_tests({target})")
    rel = target.relative_to(root).as_posix() if target != root else "."
    python = shlex.quote(sys.executable or "python3")
    return run_shell_command(f'{python} -m pytest "{rel}" -q --tb=short')


# ---------------------------------------------------------------------------
# Git helpers (inspection only — never commit/push)
# ---------------------------------------------------------------------------


def git_available() -> bool:
    if shutil.which("git") is None:
        return False
    root = get_repo_root()
    return (root / ".git").exists() or _git_rev_parse_ok(root)


def _git_rev_parse_ok(root: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return completed.returncode == 0 and "true" in (completed.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        return False


def git_status() -> Dict[str, Any]:
    """Return structured git status, or a clear unavailable result."""
    root = get_repo_root()
    print("[tool] git_status()")
    if not git_available():
        return {"available": False, "output": "", "changed_files": []}
    result = run_command("git status --porcelain")
    changed = []
    for line in (result["stdout"] or "").splitlines():
        line = line.rstrip()
        if len(line) >= 4:
            changed.append(line[3:].strip())
    return {
        "available": True,
        "output": result["stdout"],
        "changed_files": changed,
        "exit_code": result["exit_code"],
    }


def git_diff(*, staged: bool = False) -> Dict[str, Any]:
    """Return git diff text for verification evidence."""
    root = get_repo_root()
    print(f"[tool] git_diff(staged={staged})")
    if not git_available():
        return {"available": False, "diff": "", "files": [], "stat": ""}
    cmd = "git diff --staged" if staged else "git diff"
    # Also include untracked file contents summary via status; diff covers tracked changes.
    result = run_command(f"{cmd} --")
    files_result = run_command("git diff --name-only")
    files = [ln.strip() for ln in (files_result["stdout"] or "").splitlines() if ln.strip()]
    stat_result = run_command("git diff --stat")
    # Untracked files
    status = git_status()
    for path in status.get("changed_files") or []:
        if path not in files and path:
            # Check if file exists and is new
            full = root / path
            if full.is_file() and path not in files:
                files.append(path)
    return {
        "available": True,
        "diff": truncate_output(result["stdout"] or "", config.MAX_COMMAND_OUTPUT_CHARS),
        "files": files,
        "stat": truncate_output(stat_result["stdout"] or "", 2000),
        "exit_code": result["exit_code"],
    }


def git_log(n: int = 8) -> Dict[str, Any]:
    """Read-only recent history for evidence. Never mutates the repo."""
    print(f"[tool] git_log(n={n})")
    if not git_available():
        return {"available": False, "log": ""}
    limit = max(1, min(int(n), 30))
    result = run_command(f"git log -n {limit} --oneline")
    return {
        "available": True,
        "log": truncate_output(result.get("stdout") or "", 2000),
        "exit_code": result.get("exit_code"),
    }
