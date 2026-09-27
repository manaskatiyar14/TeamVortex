"""Detect and run project-appropriate test commands across ecosystems."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent import tools


def detect_test_framework(metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Detect a sensible verification command for the workspace.

    Prefers repository-defined scripts when confidently identifiable.
    Does not execute arbitrary package scripts blindly beyond known test keys.
    """
    root = tools.get_repo_root()
    meta = metadata or tools.detect_project_metadata()
    project_type = meta.get("project_type", "unknown")
    files = meta.get("files") or {}

    python = shlex.quote(sys.executable or "python3")
    candidates: List[Dict[str, str]] = []

    # Python / pytest
    if project_type == "python" or any(
        k in files for k in ("pytest.ini", "pyproject.toml", "requirements.txt", "setup.py")
    ):
        tests_dir = root / "tests"
        if tests_dir.is_dir():
            candidates.append(
                {
                    "framework": "pytest",
                    "command": f'{python} -m pytest tests -q --tb=short',
                    "confidence": "high",
                }
            )
        else:
            candidates.append(
                {
                    "framework": "pytest",
                    "command": f'{python} -m pytest -q --tb=short',
                    "confidence": "medium",
                }
            )

    # Node.js
    if "package.json" in files:
        pkg_path = root / files["package.json"]
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pkg = {}
        scripts = pkg.get("scripts") or {}
        runner = "npm test --silent"
        if (root / "pnpm-lock.yaml").is_file():
            runner = "pnpm test --silent"
        elif (root / "yarn.lock").is_file():
            runner = "yarn test --silent"
        if "test" in scripts and scripts["test"] not in (
            'echo "Error: no test specified" && exit 1',
        ):
            candidates.append(
                {
                    "framework": "npm",
                    "command": runner,
                    "confidence": "high",
                }
            )

    # Go
    if "go.mod" in files or project_type == "go":
        candidates.append(
            {
                "framework": "go",
                "command": "go test ./...",
                "confidence": "high",
            }
        )

    # Maven
    if "pom.xml" in files or project_type == "java-maven":
        candidates.append(
            {
                "framework": "maven",
                "command": "mvn test -q",
                "confidence": "high",
            }
        )

    # Gradle
    if project_type == "java-gradle" or any(
        k in files for k in ("build.gradle", "build.gradle.kts")
    ):
        gradlew = root / "gradlew"
        if gradlew.is_file():
            candidates.append(
                {
                    "framework": "gradle",
                    "command": "./gradlew test --quiet",
                    "confidence": "high",
                }
            )
        else:
            candidates.append(
                {
                    "framework": "gradle",
                    "command": "gradle test --quiet",
                    "confidence": "medium",
                }
            )

    if not candidates:
        # Last resort: try pytest if any test_*.py exists
        py_tests = [f for f in tools.list_files() if Path(f).name.startswith("test_") and f.endswith(".py")]
        if py_tests:
            candidates.append(
                {
                    "framework": "pytest",
                    "command": f'{python} -m pytest -q --tb=short',
                    "confidence": "low",
                }
            )

    primary = candidates[0] if candidates else None
    return {
        "project_type": project_type,
        "candidates": candidates,
        "primary": primary,
        "command": primary["command"] if primary else None,
        "framework": primary["framework"] if primary else None,
    }


def run_detected_tests(
    *,
    test_path: str | Path | None = None,
    override_command: str | None = None,
    detection: Optional[Dict[str, Any]] = None,
) -> Tuple[int, str, str, str]:
    """Run tests using detection or override. Returns (code, stdout, stderr, command)."""
    if override_command:
        code, out, err = tools.run_tests(command=override_command)
        return code, out, err, override_command

    if test_path is not None:
        code, out, err = tools.run_tests(test_path)
        python = shlex.quote(sys.executable or "python3")
        cmd = f'{python} -m pytest "{test_path}" -q --tb=short'
        return code, out, err, cmd

    info = detection or detect_test_framework()
    command = info.get("command")
    if not command:
        return 1, "", "No testing framework confidently detected.", ""
    code, out, err = tools.run_tests(command=command)
    return code, out, err, command


def run_targeted_tests(test_files: List[str]) -> Tuple[int, str, str, str]:
    """Run a focused pytest invocation over specific test files when they are Python."""
    py_tests = [f for f in test_files if f.endswith(".py")]
    if not py_tests:
        return run_detected_tests()
    python = shlex.quote(sys.executable or "python3")
    joined = " ".join(f'"{p}"' for p in py_tests[:10])
    command = f"{python} -m pytest {joined} -q --tb=short"
    code, out, err = tools.run_tests(command=command)
    return code, out, err, command
