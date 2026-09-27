"""Task analyzer and JSON utility tests."""

from __future__ import annotations

from agent.context import TaskType
from agent.json_util import parse_json_object
from agent.task_analyzer import (
    analyze_task,
    classify_issue,
    extract_acceptance_criteria,
    extract_issue_signals,
    summarize_task,
)


def test_analyze_explicit_modes():
    assert analyze_task("x", explicit_mode="1") == TaskType.REPOSITORY
    assert analyze_task("x", explicit_mode="standalone") == TaskType.STANDALONE


def test_analyze_repo_bug_with_path():
    assert (
        analyze_task("Fix the authentication bug in the API", has_repo=True)
        == TaskType.REPOSITORY
    )


def test_analyze_leetcode_style():
    task = (
        "LeetCode-style: write a function that returns the two-sum indices. "
        "Constraints: n <= 1e5. Example 1: ..."
    )
    assert analyze_task(task, has_repo=False) == TaskType.STANDALONE


def test_summarize_task():
    text = summarize_task("Fix add()", TaskType.REPOSITORY)
    assert "Repository" in text


def test_parse_json_object_plain():
    assert parse_json_object('{"a": 1}') == {"a": 1}


def test_parse_json_object_fenced():
    assert parse_json_object('```json\n{"files": []}\n```') == {"files": []}


def test_parse_json_object_malformed():
    assert parse_json_object("not json at all") == {}


def test_parse_json_object_embedded():
    assert parse_json_object('prefix {"files": ["a.py"]} suffix')["files"] == ["a.py"]


def test_parse_json_object_truncated():
    data = parse_json_object('{"files": [{"path": "a.py", "content": "x"}]')
    assert data.get("files")


def test_classify_issue_kinds():
    assert classify_issue("The unit tests are failing in test_add") == "TEST_FAILURE"
    assert classify_issue("Add a new endpoint for invoices") == "FEATURE"
    assert classify_issue("Please update the README typos") == "DOCUMENTATION"
    assert classify_issue("Something is wrong somewhere") == "UNKNOWN"


def test_extract_signals_sql_and_criteria():
    task = (
        'Fix the JOIN in reports.sql\n'
        'Expected: unique rows per customer\n'
        'should return one row per id'
    )
    signals = extract_issue_signals(task)
    assert any(p.endswith(".sql") for p in signals["paths"])
    assert extract_acceptance_criteria(task)
