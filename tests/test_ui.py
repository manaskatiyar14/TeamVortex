"""Tests for Terminal User Interface module (ui.py)."""

from __future__ import annotations

from pathlib import Path
from agent import ui


def test_ui_banner_and_info(capsys):
    ui.banner()
    ui.info("test info message")
    captured = capsys.readouterr().out
    assert "VORTEX" in captured
    assert "test info message" in captured


def test_ui_task_overview(capsys):
    ui.task_overview(
        task="Test task description",
        mode="Standalone Coding Problem",
        workspace="/path/to/test",
        model="groq/openai/gpt-oss-20b",
        max_attempts=5,
    )
    captured = capsys.readouterr().out
    assert "Test task description" in captured
    assert "Standalone Coding Problem" in captured


def test_ui_stages(capsys):
    stages = ["ANALYZE", "EXPLORE", "SEARCH", "READ", "PLAN", "EDIT", "TEST", "RECOVER", "VERIFY"]
    for s in stages:
        ui.stage(s, f"Testing stage {s}")
    captured = capsys.readouterr().out
    for s in stages:
        assert f"[{s}]" in captured


def test_ui_result_verified(capsys):
    ui.result(
        "VERIFIED",
        detail="All checks passed.",
        workspace="/workspace/path",
        solution_path="/workspace/path/solution.py",
        test_path="/workspace/path/test_solution.py",
        files_changed=["solution.py", "test_solution.py"],
        metrics="attempts=1 tests=2",
    )
    captured = capsys.readouterr().out
    assert "RESULT: VERIFIED" in captured
    assert "/workspace/path/solution.py" in captured
    assert "/workspace/path/test_solution.py" in captured


def test_ui_result_failed(capsys):
    ui.result("FAILED", detail="Tests did not pass.")
    captured = capsys.readouterr().out
    assert "RESULT: FAILED" in captured
