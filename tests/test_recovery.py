"""Recovery / agent-loop behavior with mocked deterministic model responses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import tools
from agent.agent_loop import run_agent
from agent.context import TaskType, VerificationStatus
from agent.recovery import capture_failure_evidence, expand_context_from_output
from agent.context import AgentContext


@pytest.fixture
def bug_repo(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "vortex@test"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Vortex"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def _responses_queue(monkeypatch, responses: list[str]):
    """Patch generate_text to return queued responses in order."""
    queue = list(responses)

    def fake_generate(prompt, *, system_instruction=None, json_mode=False):
        if not queue:
            raise AssertionError(f"Unexpected generate_text call. prompt[:200]={prompt[:200]!r}")
        return queue.pop(0)

    monkeypatch.setattr("agent.agent_loop.generate_text", fake_generate)
    monkeypatch.setattr("agent.planner.generate_text", fake_generate)
    monkeypatch.setattr("agent.recovery.generate_text", fake_generate)
    monkeypatch.setenv("AI_API_KEY", "test-key")


def test_capture_and_expand_context(bug_repo: Path):
    tools.clear_file_cache()
    tools.set_repo_root(bug_repo)
    ctx = AgentContext(
        task="fix add",
        task_type=TaskType.REPOSITORY,
        workspace=str(bug_repo),
        attempt=1,
        max_attempts=3,
        hypothesis="maybe subtraction",
        files_modified=["calculator.py"],
    )
    evidence = capture_failure_evidence(ctx, "exit_code=1\nAssertionError")
    assert "Attempt 1" in evidence
    expand_context_from_output(
        ctx,
        "Failure in calculator.py and test_calculator.py",
        ["calculator.py", "test_calculator.py"],
    )
    assert "calculator.py" in ctx.files_read


def test_agent_recovers_after_bad_first_patch(bug_repo: Path, monkeypatch):
    """First patch keeps the bug; second patch fixes it. Recovery path must succeed."""
    wrong = {
        "files": [
            {
                "path": "calculator.py",
                "content": "def add(a, b):\n    return a - b\n",
            }
        ]
    }
    # Unnecessarily "smart" wrong approach (validation nonsense) then correct minimal fix
    still_wrong = {
        "files": [
            {
                "path": "calculator.py",
                "content": (
                    "def add(a, b):\n"
                    "    if not isinstance(a, (int, float)):\n"
                    "        raise TypeError('bad')\n"
                    "    return a - b\n"
                ),
            }
        ]
    }
    fixed = {
        "files": [
            {
                "path": "calculator.py",
                "content": "def add(a, b):\n    return a + b\n",
            }
        ]
    }
    select = {"files": ["calculator.py", "test_calculator.py"]}
    plan = {
        "problem_understanding": "add returns wrong result",
        "hypothesis": "implementation subtracts instead of adding",
        "files_to_inspect": ["calculator.py"],
        "tests_to_inspect": ["test_calculator.py"],
        "expected_modifications": ["calculator.py"],
        "verification_strategy": "run pytest",
        "steps": ["inspect", "fix", "test", "verify"],
    }
    recovery = {
        "analysis": "still subtracting",
        "new_hypothesis": "replace subtraction with addition; no validation needed",
        "files_to_reread": ["calculator.py", "test_calculator.py"],
        "reason_different_approach_needed": "previous patch still used subtraction",
    }
    plan2 = dict(plan)
    plan2["hypothesis"] = recovery["new_hypothesis"]

    # Order of generate_text calls in run_agent:
    # 1 select files, 2 create plan, 3 generate fix (wrong),
    # 4 analyze failure, 5 update plan, 6 generate fix (still wrong),
    # 7 analyze failure, 8 update plan, 9 generate fix (fixed)
    _responses_queue(
        monkeypatch,
        [
            json.dumps(select),
            json.dumps(plan),
            json.dumps(wrong),
            json.dumps(recovery),
            json.dumps(plan2),
            json.dumps(still_wrong),
            json.dumps(recovery),
            json.dumps(plan2),
            json.dumps(fixed),
        ],
    )
    monkeypatch.setattr("config.MAX_RETRY_ATTEMPTS", 5)

    ok = run_agent(bug_repo, "Fix add() so add(2,3)==5", task_type=TaskType.REPOSITORY)
    assert ok is True
    content = (bug_repo / "calculator.py").read_text(encoding="utf-8")
    assert "return a + b" in content
    # Tests must remain unchanged
    assert "assert add(2, 3) == 5" in (bug_repo / "test_calculator.py").read_text(encoding="utf-8")


def test_agent_basic_bugfix_verified(bug_repo: Path, monkeypatch):
    select = {"files": ["calculator.py", "test_calculator.py"]}
    plan = {
        "problem_understanding": "add uses subtraction",
        "hypothesis": "return a - b should be a + b",
        "files_to_inspect": ["calculator.py"],
        "tests_to_inspect": ["test_calculator.py"],
        "expected_modifications": ["calculator.py"],
        "verification_strategy": "pytest",
        "steps": ["fix implementation only", "run tests", "inspect diff"],
    }
    fixed = {
        "files": [
            {
                "path": "calculator.py",
                "content": "def add(a, b):\n    return a + b\n",
            }
        ]
    }
    _responses_queue(
        monkeypatch,
        [json.dumps(select), json.dumps(plan), json.dumps(fixed)],
    )
    ok = run_agent(bug_repo, "Fix add() behavior", task_type=TaskType.REPOSITORY)
    assert ok is True
    assert "return a + b" in (bug_repo / "calculator.py").read_text(encoding="utf-8")
