"""Standalone coding-problem mode: temporary workspace, no manual repo required."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from agent import tools


SOLUTION_FILENAME = "solution.py"
TEST_FILENAME = "test_solution.py"
README_FILENAME = "PROBLEM.md"


def create_workspace(
    problem: str,
    *,
    provided_tests: str = "",
    workspace_dir: Optional[Path | str] = None,
    cleanup: Optional[bool] = None,
) -> Tuple[Path, bool]:
    """Create an isolated workspace for a standalone problem.

    When cleanup=False (the default for CLI/evaluation runs), creates a persistent
    deterministic directory under 'workspaces/standalone' so the final solution and tests
    remain accessible for evaluator inspection.
    Returns (workspace_path, should_cleanup).
    """
    if workspace_dir:
        workspace = Path(workspace_dir).resolve()
        should_cleanup = bool(cleanup) if cleanup is not None else False
    elif os.environ.get("VORTEX_WORKSPACE"):
        workspace = Path(os.environ["VORTEX_WORKSPACE"]).resolve()
        should_cleanup = bool(cleanup) if cleanup is not None else False
    elif cleanup is False:
        project_root = Path(__file__).resolve().parent.parent
        workspace = (project_root / "workspaces" / "standalone").resolve()
        should_cleanup = False
    else:
        # Fallback for unit testing where automatic cleanup is expected
        workspace = Path(tempfile.mkdtemp(prefix="vortex_standalone_"))
        should_cleanup = True if cleanup is None else bool(cleanup)

    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / README_FILENAME).write_text(problem.strip() + "\n", encoding="utf-8")

    # Starter solution — intentionally wrong/empty so the agent must implement it.
    starter = (
        '"""Solution for the standalone coding problem. Implement as required."""\n\n'
        "def solve(*args, **kwargs):\n"
        '    raise NotImplementedError("Implement solve() for the problem.")\n'
    )
    (workspace / SOLUTION_FILENAME).write_text(starter, encoding="utf-8")

    if provided_tests and provided_tests.strip():
        (workspace / TEST_FILENAME).write_text(provided_tests.strip() + "\n", encoding="utf-8")
    else:
        # Minimal placeholder so pytest discovery works; agent will extend with real tests.
        placeholder = (
            "import pytest\n"
            "from solution import solve\n\n"
            "def test_solve_is_callable():\n"
            "    assert callable(solve)\n"
        )
        (workspace / TEST_FILENAME).write_text(placeholder, encoding="utf-8")

    # Marker distinguishing test provenance for the agent/verifier messaging
    (workspace / ".vortex_standalone").write_text(
        "PROVIDED_TESTS\n" if provided_tests.strip() else "AGENT_SCAFFOLD_TESTS\n",
        encoding="utf-8",
    )
    print(f"[standalone] created workspace: {workspace}")
    return workspace, should_cleanup


def cleanup_workspace(workspace: Path) -> None:
    try:
        shutil.rmtree(workspace, ignore_errors=True)
        print(f"[standalone] cleaned workspace: {workspace}")
    except OSError as exc:
        print(f"[standalone] cleanup failed: {exc}")


def classify_test_kind(workspace: Path) -> str:
    marker = workspace / ".vortex_standalone"
    if marker.is_file():
        text = marker.read_text(encoding="utf-8", errors="replace")
        if "PROVIDED" in text:
            return "provided"
        return "agent_generated"
    return "provided"


def write_stdin_stdout_harness(workspace: Path, sample_input: str, expected_output: str) -> None:
    """Optional helper for classic competitive-programming IO problems."""
    harness = (
        "from pathlib import Path\n"
        "import subprocess\n"
        "import sys\n\n"
        f"SAMPLE_INPUT = {sample_input!r}\n"
        f"EXPECTED = {expected_output!r}\n\n"
        "def test_stdin_stdout():\n"
        "    proc = subprocess.run(\n"
        "        [sys.executable, 'solution.py'],\n"
        "        input=SAMPLE_INPUT,\n"
        "        capture_output=True,\n"
        "        text=True,\n"
        "        timeout=10,\n"
        "    )\n"
        "    assert proc.returncode == 0, proc.stderr\n"
        "    assert proc.stdout.strip() == EXPECTED.strip()\n"
    )
    (workspace / "test_io.py").write_text(harness, encoding="utf-8")
