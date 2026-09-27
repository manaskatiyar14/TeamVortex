"""Expanded tests for Vortex tools: path protection, search, git, safety."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import tools
from agent.safety import is_command_blocked, looks_like_secret, resolve_under_root


@pytest.fixture
def repo(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "hello.py").write_text(
        "VALUE = 1\n\ndef add(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_hello.py").write_text(
        "from pkg.hello import VALUE, add\n\n"
        "def test_value():\n    assert VALUE == 1\n\n"
        "def test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignore.js").write_text("x", encoding="utf-8")
    tools.clear_file_cache()
    tools.set_repo_root(tmp_path)
    return tmp_path


def test_list_files_returns_relative_paths(repo: Path):
    listed = tools.list_files()
    assert "pkg/hello.py" in listed
    assert "tests/test_hello.py" in listed
    assert not any("node_modules" in p for p in listed)


def test_read_file_uses_cache(repo: Path, capsys):
    first = tools.read_file("pkg/hello.py")
    second = tools.read_file("pkg/hello.py")
    assert "VALUE = 1" in first
    assert first == second
    captured = capsys.readouterr().out
    assert "[cache hit]" in captured


def test_write_file_updates_cache(repo: Path):
    tools.write_file("pkg/hello.py", "VALUE = 2\n")
    assert (repo / "pkg" / "hello.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert tools.read_file("pkg/hello.py") == "VALUE = 2\n"


def test_edit_file(repo: Path):
    result = tools.edit_file("pkg/hello.py", "VALUE = 1", "VALUE = 9")
    assert result["ok"] is True
    assert "VALUE = 9" in tools.read_file("pkg/hello.py")


def test_path_protection_blocks_traversal(repo: Path):
    with pytest.raises(PermissionError):
        tools.read_file("../outside.txt")
    with pytest.raises(PermissionError):
        resolve_under_root(repo, "../outside.txt")


def test_search_code_finds_symbol(repo: Path):
    hits = tools.search_code("def add")
    assert any(h["path"] == "pkg/hello.py" for h in hits)


def test_find_symbol(repo: Path):
    hits = tools.find_symbol("add")
    assert any(h["path"] == "pkg/hello.py" for h in hits)


def test_find_related_tests(repo: Path):
    related = tools.find_related_tests("pkg/hello.py")
    assert "tests/test_hello.py" in related


def test_detect_project_metadata(repo: Path):
    meta = tools.detect_project_metadata()
    assert meta["project_type"] == "python"
    assert "requirements.txt" in meta["files"]


def test_run_shell_command(repo: Path):
    code, stdout, _stderr = tools.run_shell_command("echo vortex-ok")
    assert code == 0
    assert "vortex-ok" in stdout


def test_run_command_blocks_dangerous(repo: Path):
    result = tools.run_command("rm -rf /")
    assert result["blocked"] is True
    assert result["exit_code"] == 126


def test_is_command_blocked_sudo(repo: Path):
    blocked, reason = is_command_blocked("sudo ls", repo)
    assert blocked is True
    assert "sudo" in reason.lower()


def test_looks_like_secret():
    findings = looks_like_secret('API_KEY = "sk-abcdefghijklmnopqrstuvwxyz"')
    assert findings


def test_run_tests_passes_on_sample_repo(repo: Path):
    code, stdout, stderr = tools.run_tests("tests")
    assert code == 0, stdout + stderr


def test_git_unavailable_without_repo(repo: Path):
    status = tools.git_status()
    # tmp_path is not a git repo unless initialized
    assert status["available"] is False or isinstance(status.get("changed_files"), list)
