"""Entry-point behavior: missing inputs, modes, missing API key."""

from __future__ import annotations

from pathlib import Path

from main import main


def test_missing_repo_returns_2(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["--mode", "repository", "--task", "fix something"]) == 2


def test_missing_task_returns_2(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["--mode", "repository", "--repo", str(tmp_path)]) == 2


def test_missing_api_key_returns_1_without_traceback(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    code = main(["--mode", "repository", "--repo", str(tmp_path), "--task", "fix a bug"])
    assert code == 1
    err = capsys.readouterr().err
    assert "AI_API_KEY" in err
    assert "Traceback" not in err


def test_invalid_repo_path(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("AI_API_KEY", "x")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    code = main(
        [
            "--mode",
            "repository",
            "--repo",
            str(tmp_path / "does-not-exist"),
            "--task",
            "fix",
        ]
    )
    assert code == 2
    assert "not a directory" in capsys.readouterr().err


def test_standalone_requires_task(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setenv("AI_API_KEY", "x")
    code = main(["--mode", "standalone"])
    assert code == 2
    assert "Problem statement" in capsys.readouterr().err
