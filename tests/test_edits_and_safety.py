"""Precise editing, transactional rollback, and extra safety checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import tools
from agent.failure import count_failing_tests, detect_no_progress, failing_test_progress
from agent.safety import is_command_blocked, redact_secrets, resolve_under_root


@pytest.fixture
def repo(tmp_path: Path):
    (tmp_path / "a.py").write_text("VALUE = 1\nOTHER = 2\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("OK = True\n", encoding="utf-8")
    tools.clear_file_cache()
    tools.set_repo_root(tmp_path)
    return tmp_path


def test_targeted_and_whole_file_edits(repo: Path):
    result = tools.edit_file("a.py", "VALUE = 1", "VALUE = 9")
    assert result["ok"] is True
    applied = tools.apply_file_operations(
        [{"path": "b.py", "content": "OK = False\n"}]
    )
    assert applied["ok"] is True
    assert "OK = False" in (repo / "b.py").read_text(encoding="utf-8")


def test_transactional_rollback_on_second_failure(repo: Path):
    original_a = (repo / "a.py").read_text(encoding="utf-8")
    result = tools.apply_file_operations(
        [
            {"path": "a.py", "content": "VALUE = 99\nOTHER = 2\n"},
            {"path": "../outside.py", "content": "nope"},
        ]
    )
    assert result["ok"] is False
    assert (repo / "a.py").read_text(encoding="utf-8") == original_a


def test_duplicate_path_rejected(repo: Path):
    result = tools.apply_file_operations(
        [
            {"path": "a.py", "content": "VALUE = 3\n"},
            {"path": "a.py", "content": "VALUE = 4\n"},
        ]
    )
    assert result["ok"] is False
    assert "duplicate" in result["error"]


def test_noop_rejected(repo: Path):
    current = (repo / "a.py").read_text(encoding="utf-8")
    result = tools.apply_file_operations([{"path": "a.py", "content": current}])
    assert result["ok"] is False
    assert "no-op" in result["error"]


def test_absolute_and_traversal_rejected(repo: Path):
    with pytest.raises(PermissionError):
        resolve_under_root(repo, "/etc/passwd")
    result = tools.apply_file_operations(
        [{"path": "/etc/passwd", "content": "x"}]
    )
    assert result["ok"] is False


def test_git_destructive_blocked(repo: Path):
    blocked, reason = is_command_blocked("git reset --hard", repo)
    assert blocked is True
    assert "not allowed" in reason.lower() or "reset" in reason.lower()


def test_printenv_and_api_key_blocked(repo: Path):
    blocked, _ = is_command_blocked("printenv", repo)
    assert blocked is True
    blocked, _ = is_command_blocked("echo $AI_API_KEY", repo)
    assert blocked is True
    blocked, _ = is_command_blocked("cat ~/.ssh/id_rsa", repo)
    assert blocked is True


def test_redact_secrets(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "super-secret-value-xyz")
    text = redact_secrets("using super-secret-value-xyz in logs")
    assert "super-secret-value-xyz" not in text
    assert "[REDACTED]" in text


def test_subprocess_does_not_inherit_api_key(repo: Path, monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "must-not-leak-into-repo-commands")
    result = tools.run_command("python3 -c 'import os; print(os.environ.get(\"AI_API_KEY\", \"UNSET\"))'")
    assert result["blocked"] is False
    assert result["exit_code"] == 0
    assert "must-not-leak-into-repo-commands" not in (result["stdout"] + result["stderr"])
    assert "UNSET" in result["stdout"]


def test_timeout_and_huge_output(repo: Path):
    result = tools.run_command("python3 -c 'import time; time.sleep(30)'", timeout=1)
    assert result["exit_code"] == 124
    assert "timed out" in result["stderr"]
    result = tools.run_command("python3 -c 'print(\"x\"*20000)'")
    assert "truncated" in result["stdout"] or len(result["stdout"]) <= 8000 + 50


def test_failure_progress_helpers():
    assert failing_test_progress(8, 2) == "improved"
    assert failing_test_progress(2, 4) == "worsened"
    assert failing_test_progress(3, 3) == "unchanged"
    assert detect_no_progress(["a", "a"], threshold=2) is True
    assert count_failing_tests("=== 2 failed, 3 passed in 0.1s ===") == 2
