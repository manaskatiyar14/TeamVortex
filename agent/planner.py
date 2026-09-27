"""Planner: create and update an explicit repair/implementation plan before edits."""

from __future__ import annotations

from typing import Any, Dict, List

from agent.context import UNTRUSTED_DATA_NOTICE, AgentContext, Plan
from agent.llm_client import generate_text
from agent import json_util


_MINIMAL_CHANGE_RULES = (
    "MINIMAL CHANGE PRINCIPLE (mandatory):\n"
    "- Prefer the smallest justified change that solves the stated task.\n"
    "- Do NOT invent requirements, add unrelated validation, or add features.\n"
    "- Do NOT refactor unrelated code or rewrite files unnecessarily.\n"
    "- Do NOT modify tests merely to make them pass unless the task explicitly requires it.\n"
    "- Before changing a file, know why that file must change.\n"
)


def create_plan(ctx: AgentContext, *, file_listing: str, search_hits: str = "") -> Plan:
    """Ask the model for a structured plan; fall back to a heuristic plan on failure."""
    prompt = (
        "You are planning a software-engineering fix. Return JSON only.\n"
        "Schema: {\n"
        '  "problem_understanding": "string",\n'
        '  "hypothesis": "string",\n'
        '  "files_to_inspect": ["path", ...],\n'
        '  "tests_to_inspect": ["path", ...],\n'
        '  "expected_modifications": ["path or change summary", ...],\n'
        '  "verification_strategy": "string",\n'
        '  "steps": ["step", ...]\n'
        "}\n"
        f"{_MINIMAL_CHANGE_RULES}\n"
        f"{UNTRUSTED_DATA_NOTICE}\n"
        f"TASK TYPE: {ctx.task_type.value}\n"
        + (
            "NOTE FOR STANDALONE MODE: If tests are not provided, plan to author both 'solution.py' and 'test_solution.py'.\n"
            if ctx.task_type.value == "standalone" and not ctx.provided_tests.strip()
            else ""
        )
        + f"TASK:\n{ctx.task}\n\n"
        f"PROJECT TYPE: {ctx.project_type}\n"
        f"METADATA: {ctx.repository_metadata}\n"
        f"ISSUE SIGNALS: {ctx.issue_signals}\n"
        f"REPO MAP (compact): {str(ctx.repo_map)[:1500]}\n\n"
        f"FILE LISTING (may be truncated):\n{file_listing[:6000]}\n\n"
        f"SEARCH HITS:\n{search_hits[:3000] or '(none)'}\n"
    )
    ctx.bump_model_call()
    raw = generate_text(prompt, json_mode=True)
    data = json_util.parse_json_object(raw)
    if not data:
        ctx.bump_model_call()
        raw = generate_text(
            prompt + "\n\nPrevious reply was invalid. Return only the JSON object.",
            json_mode=True,
        )
        data = json_util.parse_json_object(raw)

    plan = _plan_from_dict(data) if data else _heuristic_plan(ctx)
    _set_plan(ctx, plan, event="initial")
    return plan


def update_plan(ctx: AgentContext, *, failure_evidence: str) -> Plan:
    """Revise hypothesis/plan after a failed attempt."""
    current = ctx.plan.to_prompt_block() if ctx.plan else "(none)"
    prompt = (
        "Update the repair plan after a failed attempt. Return JSON with the same schema "
        "as before (problem_understanding, hypothesis, files_to_inspect, tests_to_inspect, "
        "expected_modifications, verification_strategy, steps).\n"
        f"{_MINIMAL_CHANGE_RULES}\n"
        f"{UNTRUSTED_DATA_NOTICE}\n"
        "Do NOT repeat the previous failing approach. Incorporate the failure evidence.\n"
        "If the failure contradicts the prior hypothesis, REPLACE the hypothesis.\n\n"
        f"TASK:\n{ctx.task}\n\n"
        f"CURRENT PLAN:\n{current}\n\n"
        f"PREVIOUS HYPOTHESIS:\n{ctx.hypothesis or '(none)'}\n\n"
        f"FAILURE EVIDENCE:\n{failure_evidence[:4000]}\n\n"
        f"FILES ALREADY READ:\n{list(ctx.files_read.keys())}\n"
        f"FILES MODIFIED:\n{ctx.files_modified}\n"
        f"ISSUE SIGNALS:\n{ctx.issue_signals}\n"
    )
    ctx.bump_model_call()
    raw = generate_text(prompt, json_mode=True)
    data = json_util.parse_json_object(raw)
    if data:
        plan = _plan_from_dict(data)
        _set_plan(ctx, plan, event="replan")
        return plan
    if ctx.plan:
        ctx.plan.hypothesis = (
            (ctx.plan.hypothesis or "")
            + " | Previous approach failed; revise based on test output."
        )
        ctx.hypothesis = ctx.plan.hypothesis
        ctx.replan_count += 1
        ctx.plan_history.append(f"replan#{ctx.replan_count}: heuristic hypothesis update")
        return ctx.plan
    return _heuristic_plan(ctx)


def _set_plan(ctx: AgentContext, plan: Plan, *, event: str) -> None:
    if ctx.plan is not None and event == "replan":
        ctx.replan_count += 1
        ctx.plan_history.append(
            f"replan#{ctx.replan_count}: hyp={plan.hypothesis[:120]!r}"
        )
    else:
        ctx.plan_history.append(f"{event}: hyp={plan.hypothesis[:120]!r}")
    ctx.plan = plan
    ctx.hypothesis = plan.hypothesis


def _plan_from_dict(data: Dict[str, Any]) -> Plan:
    def _str_list(key: str) -> List[str]:
        value = data.get(key)
        if not isinstance(value, list):
            return []
        return [str(x) for x in value if isinstance(x, (str, int))]

    return Plan(
        problem_understanding=str(data.get("problem_understanding") or ""),
        hypothesis=str(data.get("hypothesis") or ""),
        files_to_inspect=_str_list("files_to_inspect"),
        tests_to_inspect=_str_list("tests_to_inspect"),
        expected_modifications=_str_list("expected_modifications"),
        verification_strategy=str(data.get("verification_strategy") or ""),
        steps=_str_list("steps"),
    )


def _heuristic_plan(ctx: AgentContext) -> Plan:
    if getattr(ctx, "task_type", None) and ctx.task_type.value == "standalone":
        return Plan(
            problem_understanding=ctx.task,
            hypothesis="Implement solution in solution.py and verify with tests in test_solution.py.",
            files_to_inspect=["solution.py", "test_solution.py", "PROBLEM.md"],
            tests_to_inspect=["test_solution.py"],
            expected_modifications=(
                ["solution.py", "test_solution.py"]
                if not getattr(ctx, "provided_tests", "").strip()
                else ["solution.py"]
            ),
            verification_strategy="Run pytest test_solution.py; verify all test cases pass.",
            steps=[
                "Analyze problem requirements and constraints.",
                "Implement solution in solution.py.",
                "Author or verify test cases in test_solution.py.",
                "Execute pytest and debug any issues.",
                "Verify final solution and test pass.",
            ],
        )

    tests = [f for f in ctx.relevant_files if "test" in f.lower()]
    impl = [f for f in ctx.relevant_files if f not in tests]
    return Plan(
        problem_understanding=ctx.task,
        hypothesis="Inspect relevant implementation and tests; apply a minimal fix.",
        files_to_inspect=impl[:10],
        tests_to_inspect=tests[:10],
        expected_modifications=impl[:5] or ["(to be determined)"],
        verification_strategy="Run targeted tests, then broader suite; inspect diff.",
        steps=[
            "Inspect implementation and related tests.",
            "Make the minimal correction.",
            "Run relevant tests.",
            "Run broader tests if appropriate.",
            "Inspect diff and verify the task.",
        ],
    )


def minimal_change_rules() -> str:
    return _MINIMAL_CHANGE_RULES
