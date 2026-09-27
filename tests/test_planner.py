"""Planner fallback / minimal-change helpers (mocked LLM)."""

from __future__ import annotations

import json

from agent.context import AgentContext, TaskType
from agent.planner import create_plan, minimal_change_rules, update_plan


def test_minimal_change_rules_present():
    text = minimal_change_rules()
    assert "MINIMAL CHANGE" in text
    assert "Do NOT invent requirements" in text


def test_create_plan_from_model(monkeypatch):
    plan_json = {
        "problem_understanding": "add is wrong",
        "hypothesis": "uses subtraction",
        "files_to_inspect": ["calculator.py"],
        "tests_to_inspect": ["test_calculator.py"],
        "expected_modifications": ["calculator.py"],
        "verification_strategy": "pytest",
        "steps": ["inspect", "fix", "test"],
    }
    monkeypatch.setattr(
        "agent.planner.generate_text",
        lambda *a, **k: json.dumps(plan_json),
    )
    monkeypatch.setenv("AI_API_KEY", "test")
    ctx = AgentContext(
        task="Fix add",
        task_type=TaskType.REPOSITORY,
        workspace="/tmp",
        relevant_files=["calculator.py"],
    )
    plan = create_plan(ctx, file_listing="calculator.py\ntest_calculator.py")
    assert plan.hypothesis == "uses subtraction"
    assert "calculator.py" in plan.files_to_inspect


def test_create_plan_heuristic_on_bad_json(monkeypatch):
    monkeypatch.setattr("agent.planner.generate_text", lambda *a, **k: "not-json")
    monkeypatch.setenv("AI_API_KEY", "test")
    ctx = AgentContext(
        task="Fix add",
        task_type=TaskType.REPOSITORY,
        workspace="/tmp",
        relevant_files=["calculator.py", "test_calculator.py"],
    )
    plan = create_plan(ctx, file_listing="calculator.py")
    assert plan.steps
    assert "minimal" in plan.hypothesis.lower() or "Inspect" in plan.hypothesis


def test_update_plan(monkeypatch):
    monkeypatch.setattr(
        "agent.planner.generate_text",
        lambda *a, **k: json.dumps(
            {
                "problem_understanding": "still broken",
                "hypothesis": "new hyp",
                "files_to_inspect": ["a.py"],
                "tests_to_inspect": [],
                "expected_modifications": ["a.py"],
                "verification_strategy": "tests",
                "steps": ["retry"],
            }
        ),
    )
    ctx = AgentContext(task="t", task_type=TaskType.REPOSITORY, workspace="/tmp")
    plan = update_plan(ctx, failure_evidence="boom")
    assert plan.hypothesis == "new hyp"
