"""Standalone workspace behavior (no live API)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.context import TaskType
from agent.standalone import classify_test_kind, cleanup_workspace, create_workspace
from agent.agent_loop import run_agent


def test_create_and_cleanup_workspace():
    ws, should_cleanup = create_workspace(
        "Write a function solve(n) that returns n*n",
        provided_tests=(
            "from solution import solve\n\n"
            "def test_square():\n"
            "    assert solve(3) == 9\n"
        ),
    )
    assert should_cleanup is True
    assert (ws / "solution.py").is_file()
    assert (ws / "test_solution.py").is_file()
    assert classify_test_kind(ws) == "provided"
    cleanup_workspace(ws)
    assert not ws.exists()


def test_persistent_workspace_creation(tmp_path: Path):
    target = tmp_path / "custom_ws"
    ws, should_cleanup = create_workspace(
        "Write solve(x)",
        workspace_dir=target,
        cleanup=False,
    )
    assert should_cleanup is False
    assert ws == target
    assert (ws / "solution.py").is_file()
    assert (ws / "test_solution.py").is_file()
    assert (ws / "PROBLEM.md").is_file()


def test_standalone_agent_solves_with_mocks(monkeypatch):
    problem = "Implement solve(n) returning n squared."
    provided = (
        "from solution import solve\n\n"
        "def test_square():\n"
        "    assert solve(4) == 16\n"
        "\n"
        "def test_zero():\n"
        "    assert solve(0) == 0\n"
    )
    ws, _ = create_workspace(problem, provided_tests=provided)

    select = {"files": ["solution.py", "test_solution.py", "PROBLEM.md"]}
    plan = {
        "problem_understanding": "square a number",
        "hypothesis": "implement solve as n*n",
        "files_to_inspect": ["solution.py", "test_solution.py"],
        "tests_to_inspect": ["test_solution.py"],
        "expected_modifications": ["solution.py"],
        "verification_strategy": "run provided pytest",
        "steps": ["implement", "test"],
    }
    solution = {
        "files": [
            {
                "path": "solution.py",
                "content": "def solve(n):\n    return n * n\n",
            }
        ]
    }

    queue = [json.dumps(select), json.dumps(plan), json.dumps(solution)]

    def fake_generate(prompt, *, system_instruction=None, json_mode=False):
        if not queue:
            raise AssertionError("unexpected LLM call")
        return queue.pop(0)

    monkeypatch.setattr("agent.agent_loop.generate_text", fake_generate)
    monkeypatch.setattr("agent.planner.generate_text", fake_generate)
    monkeypatch.setattr("agent.recovery.generate_text", fake_generate)
    monkeypatch.setenv("AI_API_KEY", "test-key")

    try:
        ok = run_agent(
            ws,
            problem,
            task_type=TaskType.STANDALONE,
            provided_tests=provided,
            cleanup_workspace=False,
        )
        assert ok is True
        assert "n * n" in (ws / "solution.py").read_text(encoding="utf-8")
    finally:
        cleanup_workspace(ws)
