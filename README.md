# Vortex — Autonomous Software-Engineering Harness

Vortex is a **text-only autonomous software-engineering AI harness** built for the **AI Harness Hackathon 2026**.

Given a prescribed software engineering task or GitHub issue, Vortex autonomously inspects the codebase (or initializes an isolated workspace for standalone coding problems), plans a targeted fix adhering to the minimal-change principle, modifies the source code, executes tests, captures failure evidence, iteratively recovers through structured debugging, and performs evidence-based verification before declaring a result.

---

## Key Principles

- **Correctness First**: Never claim success without verified test execution.
- **Evidence Over Claims**: `RESULT: VERIFIED` requires actual file modifications and passing test execution.
- **Bounded Autonomous Recovery**: Iterative failure diagnosis, plan revision, and progressive debugging.
- **Persistent Final Artifacts**: Generated code and tests remain accessible in `workspaces/standalone/` for evaluator inspection.
- **Strict Credential Security**: `AI_API_KEY` is read strictly from the environment; never logged or exposed.
- **Pure Text-Only**: Standard LLM text prompt/completion interface; no multimodal, browser, or web dependencies.

---

## Architecture & Lifecycle Stages

When executing a task, Vortex transitions through these distinct stages:

```
[ANALYZE]   ──► Classify task mode (Repository vs. Standalone) & extract issue signals
   │
[EXPLORE]   ──► Map repository structure, detect languages & test frameworks
   │
[SEARCH]    ──► Locate relevant symbols, functions, files, and related tests
   │
[READ]      ──► Ingest targeted file contents into cached context
   │
[PLAN]      ──► Generate structured repair plan & minimal change hypothesis
   │
[EDIT]      ──► Apply transactional in-workspace file modifications
   │
[TEST]      ──► Execute targeted / repository test suite
   │
   ├─► FAIL  ──► [RECOVER] (Capture failure evidence ──► revise hypothesis ──► replan ──► EDIT again)
   │
   └─► PASS  ──► [VERIFY]  (Inspect diff, check test integrity, check for secrets)
                    │
                    ▼
              RESULT: VERIFIED | FAILED | INCONCLUSIVE
```

---

## Supported Task Modes

### Mode 1: Repository Engineering Task
- Designed for real-world software repositories (bug fixes, feature additions, issue resolution).
- Automatically discovers project types (Python, Node.js, Go, Java Maven/Gradle).
- Identifies relevant source files, related tests, and test runners.
- Modifies repository implementation files, runs test suites, recovers on failure, and verifies the diff.
- Never commits or pushes changes to Git; leaves working directory ready for inspection.

### Mode 2: Standalone Coding Problem
- Designed for self-contained algorithmic problems (e.g., LeetCode, DSA, competitive programming).
- Automatically provisions an isolated persistent workspace under `workspaces/standalone/`.
- Prepares `solution.py` and `test_solution.py`.
- Generates algorithmic implementation and comprehensive test cases (covering normal inputs, edge cases, and constraints).
- Executes pytest, iterates on any failures, and verifies final correctness.
- **Artifacts persist**: The final code remains directly inspectable at:
  - Solution: `workspaces/standalone/solution.py`
  - Tests: `workspaces/standalone/test_solution.py`

---

## Quickstart & Evaluation Workflow

The harness is fully controlled via the root **`Makefile`**:

```bash
# 1. Clone repository
git clone <repository_url>
cd <repository_directory>

# 2. Provide runtime credential via environment variable
export AI_API_KEY="<YOUR_TEST_KEY>"

# 3. Setup local virtual environment and install dependencies
make setup

# 4. Launch the Vortex Terminal User Interface
make run
```

---

## Makefile Targets

| Target | Description |
|--------|-------------|
| `make setup` | Creates local `.venv` using `python3` and installs dependencies from `requirements.txt`. |
| `make run` | Starts Vortex TUI using the local `.venv`. Prompts interactively for mode and task if arguments are omitted. |
| `make test` | Executes the complete automated test suite (mocked LLM — requires no API key). |
| `make clean` | Removes generated caches (`__pycache__`, `.pytest_cache`, `workspaces/`, `*.pyc`). |
| `make distclean` | Runs `make clean` and completely removes the local `.venv`. |

---

## Terminal User Interface (TUI)

Starting Vortex with `make run` displays the interactive Terminal User Interface:

```
╭──────────────────────────────────────────────╮
│  VORTEX — Autonomous Software Engineer       │
│  Autonomous Software-Engineering AI Harness  │
╰──────────────────────────────────────────────╯

Choose mode:

  1. Repository Engineering Task
  2. Standalone Coding Problem

Mode [1/2]:
```

### Non-Interactive / Scripted Usage

Evaluators can also bypass interactive prompts using command-line arguments:

```bash
# Standalone mode:
make run ARGS='--mode standalone --task "Implement LeetCode Problem 121: Best Time to Buy and Sell Stock."'

# Repository mode:
make run ARGS='--mode repository --repo /path/to/repo --task "Fix the failing tests in test_auth.py"'
```

---

## How Final Results Are Determined

Vortex does not simply trust model claims. Results are strictly evidence-based:

- **`RESULT: VERIFIED`**:
  - Tests were executed and exited with code 0.
  - Required implementation files were genuinely modified and contain substantive logic.
  - In standalone mode: substantive test assertions were authored and passed.
  - No unexpected/unrelated files were modified.
  - No secret keys or private credentials were leaked.
  - Final solution and test files exist and are accessible.

- **`RESULT: FAILED`**:
  - Tests failed repeatedly and could not be resolved within the retry budget (`MAX_RETRY_ATTEMPTS`).
  - No-progress streak detected (identical failure repeated without improvement).
  - Model call budget or command timeout exceeded.

- **`RESULT: INCONCLUSIVE`**:
  - No test suite was detected or could be run.
  - Test files were modified when the task did not permit test modifications.
  - Verification cannot authoritatively prove correctness.

---

## Model Configuration

The model and provider configuration is centralized in `config.py` and can also be overridden via environment variables without editing files:

```python
# config.py defaults:
LLM_PROVIDER = os.environ.get("VORTEX_PROVIDER") or "groq"
MODEL_NAME = os.environ.get("VORTEX_MODEL") or "openai/gpt-oss-20b"
TEMPERATURE = 0.2
LLM_BASE_URL = os.environ.get("VORTEX_BASE_URL") or ""
```

Supported providers:
- `groq` (default: `openai/gpt-oss-20b`)
- `openai_compatible` (any OpenAI Chat Completions compatible HTTP endpoint, using `LLM_BASE_URL`)

API credentials come exclusively from:
```bash
export AI_API_KEY="<YOUR_KEY>"
```

---

## Safety & Scoping

- **Path Scoping**: All tool reads and writes are strictly resolved and validated under the workspace root. Traversal attempts (e.g. `../`) raise `PermissionError`.
- **Dangerous Commands**: `rm -rf /`, `mkfs`, `sudo`, fork bombs, shutdown, and arbitrary environment dumping (`printenv`, `env`) are strictly blocked.
- **Git Safety**: Git operations are read-only (`status`, `diff`, `log`). Vortex will never auto-commit or auto-push.
- **Credential Redaction**: `AI_API_KEY` and detected tokens are proactively redacted from test outputs, diffs, and logs.

---

## Running Automated Tests

Run the harness test suite:

```bash
make test
```

All unit tests use mocked model responses and execute in seconds without needing an active API key or network access.
