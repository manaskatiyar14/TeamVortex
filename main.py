#!/usr/bin/env python3
"""Vortex entry point — autonomous software-engineering harness."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from agent.agent_loop import run_agent
from agent.context import TaskType
from agent.llm_client import LLMClientError, MissingAPIKeyError, ensure_api_key
from agent.standalone import create_workspace
from agent.task_analyzer import analyze_task
from agent import ui


def _prompt_if_missing(value: str | None, message: str) -> str:
    if value and value.strip():
        return value.strip()
    if not sys.stdin.isatty():
        return ""
    try:
        return input(message).strip()
    except EOFError:
        return ""


def _prompt_problem_input() -> str:
    if not sys.stdin.isatty():
        return ""
    try:
        val = input("Problem statement: ").strip()
        if not val:
            return ""
        import select
        lines = [val]
        while select.select([sys.stdin], [], [], 0.05)[0]:
            extra = sys.stdin.readline()
            if not extra:
                break
            lines.append(extra.rstrip("\r\n"))
        return "\n".join(lines).strip()
    except (EOFError, Exception):
        return ""


def _prompt_multiline(message: str) -> str:
    if not sys.stdin.isatty():
        return ""
    print(message)
    print("(End with an empty line)")
    lines = []
    try:
        while True:
            line = input()
            if line == "":
                break
            lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines).strip()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Vortex — autonomous AI coding-agent harness."
    )
    parser.add_argument(
        "--mode",
        "-m",
        choices=["repository", "standalone", "1", "2"],
        default=os.environ.get("VORTEX_MODE"),
        help="Task mode: repository (1) or standalone (2).",
    )
    parser.add_argument(
        "--repo",
        "-r",
        default=os.environ.get("REPO") or os.environ.get("REPO_PATH"),
        help="Path to the external repository (repository mode).",
    )
    parser.add_argument(
        "--task",
        "-t",
        default=os.environ.get("TASK") or os.environ.get("ISSUE"),
        help="Bug description, GitHub issue, or coding-problem statement.",
    )
    parser.add_argument(
        "--tests",
        default=os.environ.get("PROVIDED_TESTS"),
        help="Optional provided test source for standalone mode.",
    )
    parser.add_argument(
        "--test-path",
        default=os.environ.get("TEST_PATH"),
        help="Optional test target inside the workspace.",
    )
    parser.add_argument(
        "repo_positional",
        nargs="?",
        help="Optional positional repository path.",
    )
    parser.add_argument(
        "task_positional",
        nargs="?",
        help="Optional positional task/issue description.",
    )
    return parser.parse_args(argv)


def _choose_mode_interactive(explicit: str | None) -> TaskType | None:
    if explicit:
        return analyze_task("", explicit_mode=explicit, has_repo=False)

    if not sys.stdin.isatty():
        return None

    ui.banner()
    print("Choose mode:")
    print("  1. Repository Engineering Task")
    print("  2. Standalone Coding Problem")
    choice = _prompt_if_missing(None, "Mode [1/2]: ")
    if not choice:
        return None
    return analyze_task("", explicit_mode=choice, has_repo=False)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = args.repo or args.repo_positional
    task = args.task or args.task_positional

    # Non-interactive convenience: if repo+task provided, default to repository mode.
    mode: TaskType | None
    if args.mode:
        mode = analyze_task(task or "", explicit_mode=args.mode, has_repo=bool(repo))
    elif repo and task:
        mode = TaskType.REPOSITORY
    else:
        mode = _choose_mode_interactive(None)
        if mode is None and repo:
            mode = TaskType.REPOSITORY
        elif mode is None and task and not repo:
            mode = TaskType.STANDALONE
        elif mode is None:
            # Final interactive prompts without forcing mode menu when non-tty already failed
            if sys.stdin.isatty():
                mode = TaskType.REPOSITORY
            else:
                print(
                    "ERROR: Provide --mode repository|standalone along with --task "
                    "(and --repo for repository mode).",
                    file=sys.stderr,
                )
                return 2

    cleanup = False
    workspace: Path | None = None

    try:
        if mode == TaskType.STANDALONE:
            task = task or _prompt_problem_input()
            if not task:
                task = _prompt_if_missing(None, "Problem statement: ")
            if not task:
                print(
                    "ERROR: Problem statement is required for standalone mode.",
                    file=sys.stderr,
                )
                return 2
            provided = args.tests or ""
            if not provided and sys.stdin.isatty() and not args.task and not args.task_positional:
                more = _prompt_if_missing(
                    None,
                    "Optional path to provided tests file (Enter to skip): ",
                )
                if more:
                    p = Path(more).expanduser()
                    if p.is_file():
                        provided = p.read_text(encoding="utf-8")
                    else:
                        print(f"WARNING: tests file not found: {p}", file=sys.stderr)
            workspace, cleanup = create_workspace(task, provided_tests=provided, cleanup=False)
            ensure_api_key()
            ok = run_agent(
                workspace,
                task,
                test_path=args.test_path,
                task_type=TaskType.STANDALONE,
                provided_tests=provided,
                cleanup_workspace=False,
            )
        else:
            # Repository mode
            repo = repo or _prompt_if_missing(None, "Repository path: ")
            task = task or _prompt_if_missing(None, "Task / issue description: ")
            if not repo:
                print(
                    "ERROR: Repository path is required. "
                    "Usage: python main.py --mode repository --repo /path/to/repo "
                    '--task "Fix the failing tests"',
                    file=sys.stderr,
                )
                return 2
            if not task:
                print(
                    "ERROR: Task/issue description is required. "
                    "Usage: python main.py --mode repository --repo /path/to/repo "
                    '--task "Fix the failing tests"',
                    file=sys.stderr,
                )
                return 2

            repo_path = Path(repo).expanduser()
            if not repo_path.exists() or not repo_path.is_dir():
                print(f"ERROR: Repository path is not a directory: {repo_path}", file=sys.stderr)
                return 2

            ensure_api_key()
            ok = run_agent(
                repo_path,
                task,
                test_path=args.test_path,
                task_type=TaskType.REPOSITORY,
            )
    except MissingAPIKeyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except LLMClientError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except PermissionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ERROR: Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Expected operational failures should be caught above; keep a clear last resort.
        print(f"ERROR: Unexpected failure: {exc}", file=sys.stderr)
        return 1

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
