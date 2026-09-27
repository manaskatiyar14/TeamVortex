"""Tests for multi-language test-framework detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import tools
from agent.test_framework import detect_test_framework


@pytest.fixture
def bind(tmp_path: Path):
    def _bind():
        tools.clear_file_cache()
        tools.set_repo_root(tmp_path)
        return tmp_path

    return _bind


def test_detect_pytest(bind, tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    bind()
    info = detect_test_framework()
    assert info["framework"] == "pytest"
    assert "pytest" in (info["command"] or "")


def test_detect_npm(bind, tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "node test.js"}}',
        encoding="utf-8",
    )
    bind()
    info = detect_test_framework()
    assert info["framework"] == "npm"
    assert "npm test" in (info["command"] or "")


def test_detect_go(bind, tmp_path: Path):
    (tmp_path / "go.mod").write_text("module example\n\ngo 1.22\n", encoding="utf-8")
    bind()
    info = detect_test_framework()
    assert info["framework"] == "go"
    assert "go test" in (info["command"] or "")


def test_detect_maven(bind, tmp_path: Path):
    (tmp_path / "pom.xml").write_text("<project></project>\n", encoding="utf-8")
    bind()
    info = detect_test_framework()
    assert info["framework"] == "maven"
    assert "mvn test" in (info["command"] or "")


def test_detect_gradle_wrapper(bind, tmp_path: Path):
    (tmp_path / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    bind()
    info = detect_test_framework()
    assert info["framework"] == "gradle"
    assert "./gradlew test" in (info["command"] or "")
