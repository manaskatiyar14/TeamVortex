"""End-to-end scenario validation for Standalone LeetCode 121 and Failure/Recovery loop."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from agent import tools
from agent.agent_loop import run_agent
from agent.context import TaskType, VerificationStatus
from agent.standalone import create_workspace


def test_e2e_leetcode_121_standalone(tmp_path: Path, monkeypatch):
    """Full end-to-end simulation of LeetCode 121 in Standalone mode."""
    task = "Implement LeetCode Problem 121: Best Time to Buy and Sell Stock."
    ws, cleanup = create_workspace(task, workspace_dir=tmp_path / "standalone_leetcode", cleanup=False)
    assert cleanup is False

    select_response = {
        "files": ["solution.py", "test_solution.py", "PROBLEM.md"]
    }
    plan_response = {
        "problem_understanding": "Find maximum profit from buying and selling stock on different days",
        "hypothesis": "Track minimum price seen so far and maximum profit achievable",
        "files_to_inspect": ["solution.py", "test_solution.py"],
        "tests_to_inspect": ["test_solution.py"],
        "expected_modifications": ["solution.py", "test_solution.py"],
        "verification_strategy": "Run pytest test_solution.py covering standard, decreasing, and edge cases",
        "steps": ["Implement maxProfit in solution.py", "Write pytest test cases in test_solution.py", "Verify with pytest"],
    }
    solution_code = (
        "def maxProfit(prices: list[int]) -> int:\n"
        "    if not prices:\n"
        "        return 0\n"
        "    min_price = float('inf')\n"
        "    max_profit = 0\n"
        "    for p in prices:\n"
        "        if p < min_price:\n"
        "            min_price = p\n"
        "        elif p - min_price > max_profit:\n"
        "            max_profit = p - min_price\n"
        "    return int(max_profit)\n"
    )
    test_code = (
        "import pytest\n"
        "from solution import maxProfit\n\n"
        "def test_example_1():\n"
        "    assert maxProfit([7, 1, 5, 3, 6, 4]) == 5\n\n"
        "def test_example_2():\n"
        "    assert maxProfit([7, 6, 4, 3, 1]) == 0\n\n"
        "def test_single_day():\n"
        "    assert maxProfit([5]) == 0\n\n"
        "def test_empty():\n"
        "    assert maxProfit([]) == 0\n"
    )
    fix_response = {
        "files": [
            {"path": "solution.py", "content": solution_code},
            {"path": "test_solution.py", "content": test_code},
        ]
    }

    queue = [
        json.dumps(select_response),
        json.dumps(plan_response),
        json.dumps(fix_response),
    ]

    def fake_generate(prompt, *, system_instruction=None, json_mode=False):
        if not queue:
            raise AssertionError(f"Unexpected generate_text call: {prompt[:150]}")
        return queue.pop(0)

    monkeypatch.setattr("agent.agent_loop.generate_text", fake_generate)
    monkeypatch.setattr("agent.planner.generate_text", fake_generate)
    monkeypatch.setattr("agent.recovery.generate_text", fake_generate)
    monkeypatch.setenv("AI_API_KEY", "mock-evaluator-key")

    ok = run_agent(
        ws,
        task,
        task_type=TaskType.STANDALONE,
        cleanup_workspace=False,
    )

    assert ok is True
    # Verify final files exist and persist
    sol_file = ws / "solution.py"
    t_file = ws / "test_solution.py"
    assert sol_file.is_file()
    assert t_file.is_file()
    assert "maxProfit" in sol_file.read_text(encoding="utf-8")
    assert "test_example_1" in t_file.read_text(encoding="utf-8")


def test_e2e_recovery_loop_on_initial_failure(tmp_path: Path, monkeypatch):
    """Test failure detection, evidence capture, replanning, and successful recovery."""
    task = "Implement solve(numbers) returning the maximum product of two numbers."
    ws, cleanup = create_workspace(task, workspace_dir=tmp_path / "standalone_recovery", cleanup=False)

    select_response = {"files": ["solution.py", "test_solution.py"]}
    plan_response = {
        "problem_understanding": "find max product of two numbers",
        "hypothesis": "multiply first two numbers (flawed initial attempt)",
        "files_to_inspect": ["solution.py", "test_solution.py"],
        "tests_to_inspect": ["test_solution.py"],
        "expected_modifications": ["solution.py", "test_solution.py"],
        "verification_strategy": "pytest",
        "steps": ["implement", "test"],
    }
    # Attempt 1: flawed solution (only multiplies first two) and tests that will fail it
    flawed_solution = (
        "def solve(nums: list[int]) -> int:\n"
        "    return nums[0] * nums[1]\n"
    )
    tests = (
        "import pytest\n"
        "from solution import solve\n\n"
        "def test_basic():\n"
        "    assert solve([3, 4, 5, 2]) == 20\n\n"
        "def test_negative():\n"
        "    assert solve([-10, -10, 5, 2]) == 100\n"
    )
    attempt1_response = {
        "files": [
            {"path": "solution.py", "content": flawed_solution},
            {"path": "test_solution.py", "content": tests},
        ]
    }

    # Recovery response after attempt 1 fails
    recovery_analysis = {
        "analysis": "solve([3,4,5,2]) returned 12 instead of 20 because it only multiplied the first two elements",
        "new_hypothesis": "sort array or find the two largest positive and two smallest negative numbers",
        "files_to_reread": ["solution.py", "test_solution.py"],
        "reason_different_approach_needed": "Must consider all pairs, not just the first two elements",
    }
    updated_plan = {
        "problem_understanding": "find max product of two numbers",
        "hypothesis": "sort numbers and take max(nums[-1]*nums[-2], nums[0]*nums[1])",
        "files_to_inspect": ["solution.py"],
        "tests_to_inspect": ["test_solution.py"],
        "expected_modifications": ["solution.py"],
        "verification_strategy": "pytest",
        "steps": ["sort and calculate", "test"],
    }
    # Attempt 2: correct implementation
    correct_solution = (
        "def solve(nums: list[int]) -> int:\n"
        "    s = sorted(nums)\n"
        "    return max(s[-1] * s[-2], s[0] * s[1])\n"
    )
    attempt2_response = {
        "files": [
            {"path": "solution.py", "content": correct_solution},
        ]
    }

    queue = [
        json.dumps(select_response),
        json.dumps(plan_response),
        json.dumps(attempt1_response),
        json.dumps(recovery_analysis),
        json.dumps(updated_plan),
        json.dumps(attempt2_response),
    ]

    def fake_generate(prompt, *, system_instruction=None, json_mode=False):
        if not queue:
            raise AssertionError(f"Unexpected generate_text call: {prompt[:150]}")
        return queue.pop(0)

    monkeypatch.setattr("agent.agent_loop.generate_text", fake_generate)
    monkeypatch.setattr("agent.planner.generate_text", fake_generate)
    monkeypatch.setattr("agent.recovery.generate_text", fake_generate)
    monkeypatch.setenv("AI_API_KEY", "mock-evaluator-key")

    ok = run_agent(
        ws,
        task,
        task_type=TaskType.STANDALONE,
        cleanup_workspace=False,
    )

    assert ok is True
    final_content = (ws / "solution.py").read_text(encoding="utf-8")
    assert "sorted(nums)" in final_content
