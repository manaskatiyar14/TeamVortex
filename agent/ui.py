"""Terminal User Interface for Vortex stages. Rich optional; plain fallback always works."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import config

_RICH = None
if getattr(config, "USE_RICH", True):
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        _RICH = Console()
    except Exception:
        _RICH = None


def banner() -> None:
    title = "VORTEX — Autonomous Software Engineer"
    subtitle = "Autonomous Software-Engineering AI Harness"
    if _RICH:
        panel = Panel.fit(
            f"[bold cyan]{title}[/bold cyan]\n[dim]{subtitle}[/dim]",
            border_style="cyan",
            padding=(0, 2),
        )
        _RICH.print(panel)
    else:
        print("=" * 64)
        print(f" {title}")
        print(f" {subtitle}")
        print("=" * 64)


def task_overview(
    *,
    task: str,
    mode: str,
    workspace: str | Path,
    model: str,
    max_attempts: int,
) -> None:
    """Display the task configuration block."""
    if _RICH:
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Key", style="bold cyan", no_wrap=True)
        table.add_column("Value", style="white")

        preview = " ".join(task.split())
        if len(preview) > 120:
            preview = preview[:117] + "..."

        table.add_row("Task:", preview)
        table.add_row("Mode:", mode)
        table.add_row("Workspace:", str(workspace))
        table.add_row("Model/Provider:", model)
        table.add_row("Max Retries:", str(max_attempts))
        _RICH.print(Panel(table, title="[bold]Run Configuration[/bold]", border_style="blue"))
    else:
        print("-" * 60)
        print(f"Task:         {task}")
        print(f"Mode:         {mode}")
        print(f"Workspace:    {workspace}")
        print(f"Model:        {model}")
        print(f"Max Retries:  {max_attempts}")
        print("-" * 60)


def stage(name: str, message: str = "") -> None:
    """Display a distinct stage tag matching the hackathon requirements."""
    label = f"[{name}]"
    if _RICH:
        color_map = {
            "ANALYZE": "bold cyan",
            "EXPLORE": "bold blue",
            "SEARCH": "bold magenta",
            "READ": "bold dodger_blue2",
            "CONTEXT": "bold dodger_blue2",
            "PLAN": "bold yellow",
            "EDIT": "bold cyan",
            "TEST": "bold green" if "passed" in message.lower() else ("bold red" if "fail" in message.lower() else "bold white"),
            "RECOVER": "bold dark_orange",
            "VERIFY": "bold green" if "verified" in message.lower() else "bold yellow",
        }
        style = color_map.get(name, "bold cyan")
        _RICH.print(f"[{style}]{label}[/{style}] {message}".rstrip())
    else:
        print(f"{label} {message}".rstrip())


def info(message: str) -> None:
    if _RICH:
        _RICH.print(message)
    else:
        print(message)


def result(
    status: str,
    detail: str = "",
    *,
    workspace: Optional[str | Path] = None,
    solution_path: Optional[str | Path] = None,
    test_path: Optional[str | Path] = None,
    files_changed: Optional[list[str]] = None,
    metrics: str = "",
) -> None:
    """Display the final evidence-based result banner and artifact paths."""
    status_clean = str(status).strip()
    header_line = f"RESULT: {status_clean}"

    if _RICH:
        style = "bold green" if status_clean == "VERIFIED" else ("bold yellow" if status_clean == "INCONCLUSIVE" else "bold red")
        border = "green" if status_clean == "VERIFIED" else ("yellow" if status_clean == "INCONCLUSIVE" else "red")

        body_parts = [f"[{style}]{header_line}[/{style}]"]

        if workspace:
            body_parts.append(f"\n[bold]Final workspace:[/bold]\n{workspace}")
        if solution_path:
            body_parts.append(f"\n[bold]Solution:[/bold]\n{solution_path}")
        if test_path:
            body_parts.append(f"\n[bold]Tests:[/bold]\n{test_path}")
        if files_changed:
            body_parts.append(f"\n[bold]Files changed:[/bold]\n{', '.join(files_changed)}")
        if metrics:
            body_parts.append(f"\n[bold]Metrics:[/bold]\n{metrics}")

        panel = Panel(
            "\n".join(body_parts),
            border_style=border,
            title="[bold]Execution Summary[/bold]",
            padding=(1, 2),
        )
        _RICH.print(panel)
        if detail:
            _RICH.print(Panel(detail, title="[dim]Verification Report[/dim]", border_style="dim"))
    else:
        print("\n" + "=" * 64)
        print(header_line)
        print("=" * 64)
        if workspace:
            print(f"\nFinal workspace:\n{workspace}")
        if solution_path:
            print(f"\nSolution:\n{solution_path}")
        if test_path:
            print(f"\nTests:\n{test_path}")
        if files_changed:
            print(f"\nFiles changed:\n{', '.join(files_changed)}")
        if metrics:
            print(f"\nMetrics:\n{metrics}")
        if detail:
            print("\n" + "-" * 40)
            print(detail)
            print("-" * 40)
