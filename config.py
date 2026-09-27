"""Vortex configuration — model/provider must be set here explicitly (never silent swap)."""

import os

# ---------------------------------------------------------------------------
# Model adapter (text-only). Organizers may prescribe a different family —
# change these values or override via environment variables.
# Do NOT hard-code credentials here. Credentials MUST come from AI_API_KEY.
# ---------------------------------------------------------------------------
LLM_PROVIDER = os.environ.get("VORTEX_PROVIDER") or os.environ.get("LLM_PROVIDER") or "groq"  # groq | openai_compatible
MODEL_NAME = os.environ.get("VORTEX_MODEL") or os.environ.get("MODEL_NAME") or "openai/gpt-oss-20b"
TEMPERATURE = float(os.environ.get("VORTEX_TEMPERATURE", "0.2"))
# Used only when LLM_PROVIDER is openai_compatible (Chat Completions HTTP API).
LLM_BASE_URL = os.environ.get("VORTEX_BASE_URL") or os.environ.get("LLM_BASE_URL") or ""

# Bounded retries for the agent edit/test loop
MAX_RETRY_ATTEMPTS = 5

# LLM transport retries (rate limits / transient failures)
LLM_MAX_RETRIES = 3
LLM_RETRY_BACKOFF_SECONDS = 1.5

# Resource budgets (stop gracefully; never infinite loops)
MAX_MODEL_CALLS = 30
MAX_SEARCH_QUERIES = 40
NO_PROGRESS_ABORT_STREAK = 3

# Command / tool limits
COMMAND_TIMEOUT_SECONDS = 60
MAX_COMMAND_OUTPUT_CHARS = 8000
MAX_FILE_CHARS = 4000
MAX_SEARCH_RESULTS = 40
MAX_CONTEXT_FILES = 20
MAX_FILE_LISTING_CHARS = 8000
MAX_SYMBOL_SKIM_FILES = 40
LARGE_REPO_FILE_THRESHOLD = 200
MAX_RAW_FILE_CHARS = 200_000

# Directories skipped during repository walks
SKIP_DIR_NAMES = {
    "__pycache__",
    ".git",
    "venv",
    ".venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "htmlcov",
    "target",
    "vendor",
    ".next",
    ".nuxt",
    ".cache",
    ".turbo",
    "bower_components",
    "site-packages",
}

# Metadata filenames used for project-type detection
METADATA_FILENAMES = (
    "README.md",
    "README.rst",
    "README",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "pytest.ini",
    "tox.ini",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "tsconfig.json",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Makefile",
    "Cargo.toml",
)

# Optional Rich terminal UI (falls back to plain print if unavailable)
USE_RICH = True
