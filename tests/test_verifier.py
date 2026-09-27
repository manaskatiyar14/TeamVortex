"""Verification logic tests (no live API)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import tools
from agent.context import AgentContext, TaskType, TestResult, VerificationStatus
from agent.verifier import verify


@pytest.fixture
def git_repo(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "vortex@test"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Vortex Test"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    # Fix the bug
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    tools.clear_file_cache()
    tools.set_repo_root(tmp_path)
    return tmp_path


def test_verify_relevant_impl_change(git_repo: Path):
    ctx = AgentContext(
        task="Fix add() so it returns the sum",
        task_type=TaskType.REPOSITORY,
        workspace=str(git_repo),
        relevant_files=["calculator.py", "test_calculator.py"],
        files_modified=["calculator.py"],
        files_read={
            "calculator.py": "def add(a, b):\n    return a + b\n",
            "test_calculator.py": "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        },
    )
    ctx.test_results.append(
        TestResult(passed=True, exit_code=0, stdout="1 passed", stderr="", command="pytest")
    )
    status = verify(ctx)
    assert status == VerificationStatus.VERIFIED
    assert "calculator.py" in ctx.verification_report


def test_verify_fails_when_tests_fail(git_repo: Path):
    ctx = AgentContext(
        task="Fix add()",
        task_type=TaskType.REPOSITORY,
        workspace=str(git_repo),
        files_modified=["calculator.py"],
        relevant_files=["calculator.py"],
    )
    ctx.test_results.append(
        TestResult(passed=False, exit_code=1, stdout="", stderr="failed", command="pytest")
    )
    status = verify(ctx)
    assert status == VerificationStatus.FAILED


def test_standalone_verified_with_substantive_tests(tmp_path: Path):
    sol_file = tmp_path / "solution.py"
    test_file = tmp_path / "test_solution.py"
    sol_file.write_text("def maxProfit(prices):\n    return 5\n", encoding="utf-8")
    test_file.write_text(
        "from solution import maxProfit\n\n"
        "def test_example1():\n    assert maxProfit([7, 1, 5, 3, 6, 4]) == 5\n\n"
        "def test_example2():\n    assert maxProfit([7, 6, 4, 3, 1]) == 0\n",
        encoding="utf-8",
    )
    tools.clear_file_cache()
    tools.set_repo_root(tmp_path)

    ctx = AgentContext(
        task="Implement LeetCode 121: Best Time to Buy and Sell Stock",
        task_type=TaskType.STANDALONE,
        workspace=str(tmp_path),
        files_modified=["solution.py", "test_solution.py"],
        relevant_files=["solution.py", "test_solution.py"],
        files_read={
            "solution.py": sol_file.read_text(encoding="utf-8"),
            "test_solution.py": test_file.read_text(encoding="utf-8"),
        },
        provided_tests="",
        standalone_test_kind="agent_generated",
    )
    ctx.test_results.append(
        TestResult(passed=True, exit_code=0, stdout="2 passed", stderr="", command="pytest")
    )
    status = verify(ctx)
    assert status == VerificationStatus.VERIFIED


def test_standalone_inconclusive_with_only_scaffold_test(tmp_path: Path):
    sol_file = tmp_path / "solution.py"
    test_file = tmp_path / "test_solution.py"
    sol_file.write_text("def solve():\n    return 0\n", encoding="utf-8")
    test_file.write_text(
        "import pytest\nfrom solution import solve\ndef test_solve_is_callable():\n    assert callable(solve)\n",
        encoding="utf-8",
    )
    tools.clear_file_cache()
    tools.set_repo_root(tmp_path)

    ctx = AgentContext(
        task="Implement algorithm",
        task_type=TaskType.STANDALONE,
        workspace=str(tmp_path),
        files_modified=["solution.py"],
        relevant_files=["solution.py", "test_solution.py"],
        files_read={
            "solution.py": sol_file.read_text(encoding="utf-8"),
            "test_solution.py": test_file.read_text(encoding="utf-8"),
        },
        provided_tests="",
        standalone_test_kind="agent_generated",
    )
    ctx.test_results.append(
        TestResult(passed=True, exit_code=0, stdout="1 passed", stderr="", command="pytest")
    )
    status = verify(ctx)
    assert status == VerificationStatus.INCONCLUSIVE


def test_standalone_fails_when_not_implemented(tmp_path: Path):
    sol_file = tmp_path / "solution.py"
    test_file = tmp_path / "test_solution.py"
    sol_file.write_text('def solve():\n    raise NotImplementedError("stub")\n', encoding="utf-8")
    test_file.write_text(
        "from solution import solve\ndef test_solve():\n    assert solve() == 1\n",
        encoding="utf-8",
    )
    tools.clear_file_cache()
    tools.set_repo_root(tmp_path)

    ctx = AgentContext(
        task="Implement algorithm",
        task_type=TaskType.STANDALONE,
        workspace=str(tmp_path),
        files_modified=["solution.py"],
        relevant_files=["solution.py", "test_solution.py"],
        files_read={
            "solution.py": sol_file.read_text(encoding="utf-8"),
            "test_solution.py": test_file.read_text(encoding="utf-8"),
        },
        provided_tests="",
        standalone_test_kind="agent_generated",
    )
    ctx.test_results.append(
        TestResult(passed=False, exit_code=1, stdout="", stderr="failed", command="pytest")
    )
    status = verify(ctx)
    assert status == VerificationStatus.FAILED
