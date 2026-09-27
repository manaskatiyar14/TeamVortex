"""Post-test verification: evidence over claims. Tests passing alone is not enough."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Set

from agent.baseline import classify_changes, is_noise_path
from agent.context import AgentContext, TaskType, VerificationStatus
from agent.failure import count_failing_tests, failing_test_progress
from agent.safety import looks_like_secret
from agent import tools


def verify(ctx: AgentContext, *, task_requires_test_changes: bool = False) -> VerificationStatus:
    """Inspect diff/status and produce VERIFIED / FAILED / INCONCLUSIVE."""
    lines: List[str] = []
    status = VerificationStatus.VERIFIED
    problems: List[str] = []

    last = ctx.test_results[-1] if ctx.test_results else None
    if getattr(ctx, "no_tests_detected", False) or (last and last.test_kind == "none"):
        problems.append("No repository tests were executed.")
        status = VerificationStatus.INCONCLUSIVE
    elif last is None:
        problems.append("No test results recorded.")
        status = VerificationStatus.FAILED
    elif not last.passed:
        problems.append("Tests did not pass.")
        status = VerificationStatus.FAILED

    if ctx.baseline_test is not None and last is not None:
        before = count_failing_tests(
            (ctx.baseline_test.stdout or "") + "\n" + (ctx.baseline_test.stderr or "")
        )
        after = count_failing_tests((last.stdout or "") + "\n" + (last.stderr or ""))
        trend = failing_test_progress(before, after)
        lines.append(f"Baseline vs current failing-test trend: {trend} (before={before}, after={after})")
        if trend == "worsened" and last.passed is False:
            problems.append("New regressions versus baseline failing-test count.")
            status = VerificationStatus.FAILED
        elif ctx.baseline_test.passed and last.passed and not ctx.files_modified:
            problems.append("Tests already passed before Vortex changed anything.")
            if status == VerificationStatus.VERIFIED:
                status = VerificationStatus.INCONCLUSIVE

    # Require both targeted and broader when both were run
    targeted = [t for t in ctx.test_results if t.targeted]
    broader = [t for t in ctx.test_results if not t.targeted]
    if targeted and targeted[-1].passed:
        lines.append("Targeted tests passed.")
    if broader:
        if broader[-1].passed:
            lines.append("Broader test suite passed.")
        else:
            problems.append("Broader test suite did not pass.")
            status = VerificationStatus.FAILED

    git_info = tools.git_status()
    diff_info = tools.git_diff()
    changed: List[str] = []
    if git_info.get("available"):
        changed = list(
            dict.fromkeys(
                (git_info.get("changed_files") or []) + (diff_info.get("files") or [])
            )
        )
        lines.append("Git available: yes")
    else:
        lines.append("Git available: no — verifying from agent-recorded modifications.")
        changed = list(ctx.files_modified)

    # Baseline attribution
    pre_existing: List[str] = []
    vortex_caused: List[str] = list(ctx.files_modified)
    if ctx.baseline is not None:
        classified = classify_changes(
            ctx.baseline,
            current_changed=changed,
            vortex_modified=ctx.files_modified,
        )
        pre_existing = classified["pre_existing"]
        vortex_caused = classified["caused_by_vortex"]
        lines.append(f"Pre-existing changes (ignored for attribution): {pre_existing or '(none)'}")
        lines.append(f"Changes caused by this Vortex run: {vortex_caused or '(none)'}")
        # Verification focuses on Vortex-caused changes
        changed_for_checks = vortex_caused
    else:
        changed_for_checks = [p for p in changed if not is_noise_path(p)]
        lines.append(f"Changed files: {changed_for_checks or '(none detected)'}")

    if diff_info.get("available") and diff_info.get("diff"):
        # Prefer showing only Vortex-touched file diffs when possible
        if diff_info.get("stat"):
            lines.append("Diff stat:")
            lines.append(str(diff_info["stat"])[:1500])
        lines.append("Diff inspected (truncated):")
        lines.append(str(diff_info["diff"])[:3000])

    log_info = tools.git_log(8) if hasattr(tools, "git_log") else {"available": False}
    if log_info.get("available") and log_info.get("log"):
        lines.append("Recent git log (evidence only):")
        lines.append(str(log_info["log"])[:800])

    expected = set(_normalize_paths(ctx.files_modified))
    if ctx.plan:
        expected |= set(_normalize_paths(ctx.plan.expected_modifications))
        expected |= set(_normalize_paths(ctx.plan.files_to_inspect))
    relevant = set(_normalize_paths(ctx.relevant_files))

    unrelated = []
    for path in changed_for_checks:
        if is_noise_path(path):
            continue
        if path in expected or path in relevant or _path_related(path, expected | relevant):
            continue
        unrelated.append(path)

    if unrelated:
        problems.append(f"Unexpected/unrelated Vortex modifications: {unrelated}")
        if status == VerificationStatus.VERIFIED:
            status = VerificationStatus.INCONCLUSIVE

    # Test file modifications by Vortex only
    test_changes = [p for p in changed_for_checks if _looks_like_test(p)]
    if ctx.task_type == TaskType.STANDALONE and not ctx.provided_tests.strip():
        # In standalone mode without provided tests, authoring test_solution.py is expected
        lines.append(f"Standalone tests authored by Vortex: {test_changes or '(none)'}")
    elif test_changes and not task_requires_test_changes:
        if not _task_mentions_tests(ctx.task):
            problems.append(f"Tests were modified but task did not require it: {test_changes}")
            if status == VerificationStatus.VERIFIED:
                status = VerificationStatus.INCONCLUSIVE
        lines.append(f"Test files touched by Vortex: {test_changes}")
    else:
        lines.append(
            "Tests unchanged by Vortex."
            if not test_changes
            else f"Test changes allowed: {test_changes}"
        )

    # Secrets — inspect Vortex-touched content
    blob = ""
    for path in vortex_caused:
        blob += ctx.files_read.get(path, "")
    if not blob and diff_info.get("available") and diff_info.get("diff"):
        blob = str(diff_info["diff"])
    secrets = looks_like_secret(blob)
    if secrets:
        problems.append(f"Possible secrets introduced: {secrets}")
        status = VerificationStatus.FAILED

    # Debug leftovers
    if re.search(r"\b(pdb\.set_trace\(|breakpoint\(|console\.log\(|TODO VORTEX)\b", blob):
        problems.append("Possible debug leftovers in Vortex changes.")
        if status == VerificationStatus.VERIFIED:
            status = VerificationStatus.INCONCLUSIVE

    impl_changed = [p for p in changed_for_checks if not _looks_like_test(p) and not is_noise_path(p)]
    lines.append(f"Implementation files changed by Vortex: {impl_changed or '(none)'}")

    if last and last.passed:
        lines.append("Intended tests passed.")
        if last.command:
            lines.append(f"Verification command: {last.command}")

    if not vortex_caused:
        if last and last.passed:
            problems.append("Tests passed but Vortex recorded no file modifications.")
            if status == VerificationStatus.VERIFIED:
                status = VerificationStatus.INCONCLUSIVE
        else:
            problems.append("No Vortex file modifications recorded.")

    criteria = ctx.acceptance_criteria or (ctx.issue_signals or {}).get("acceptance_criteria") or []
    if criteria:
        lines.append("Acceptance criteria considered:")
        for item in criteria[:8]:
            lines.append(f"- {item}")

    # Standalone evidence-based verification
    if ctx.task_type == TaskType.STANDALONE:
        kind = ctx.standalone_test_kind or (
            "provided" if ctx.provided_tests.strip() else "agent_generated"
        )
        lines.append(f"Standalone test provenance: {kind}")
        solution_content = ctx.files_read.get("solution.py", "")
        test_content = ctx.files_read.get("test_solution.py", "")

        if "raise NotImplementedError" in solution_content:
            problems.append("solution.py still contains NotImplementedError stub.")
            status = VerificationStatus.FAILED
        elif len(solution_content.strip()) < 20:
            problems.append("solution.py is empty or trivial.")
            status = VerificationStatus.FAILED

        if kind == "agent_generated":
            is_scaffold_only = (
                "test_solve_is_callable" in test_content
                and test_content.count("assert ") <= 1
                and test_content.count("def test_") <= 1
            )
            if is_scaffold_only:
                problems.append("Only trivial scaffold test present — substantive tests were not authored.")
                if status == VerificationStatus.VERIFIED:
                    status = VerificationStatus.INCONCLUSIVE
            else:
                lines.append("Substantive standalone test suite verified.")

    if problems:
        lines.append("Issues:")
        for p in problems:
            lines.append(f"- {p}")
        if status == VerificationStatus.VERIFIED:
            status = VerificationStatus.INCONCLUSIVE
    else:
        lines.append("No unrelated Vortex modifications detected.")
        lines.append("No credentials/secrets detected in Vortex changes.")

    report = (
        f"{status.value}\n\n"
        f"Task:\n{ctx.task}\n\n"
        f"Files changed by Vortex:\n{', '.join(vortex_caused) if vortex_caused else '(none)'}\n"
        f"Pre-existing changes:\n{', '.join(pre_existing) if pre_existing else '(none)'}\n\n"
        f"Verification:\n" + "\n".join(lines) + f"\n\nMetrics:\n{ctx.metrics_summary()}"
    )
    ctx.verification_status = status
    ctx.verification_report = report
    return status


def _normalize_paths(items: List[str]) -> List[str]:
    out = []
    for item in items or []:
        if not isinstance(item, str):
            continue
        token = item.strip().split()[0] if item.strip() else ""
        if "/" in token or token.endswith((".py", ".js", ".ts", ".go", ".java", ".rb", ".rs")):
            out.append(token.lstrip("./"))
        elif re.match(r"^[\w.-]+$", token) and "." in token:
            out.append(token)
    return out


def _looks_like_test(path: str) -> bool:
    p = path.lower()
    name = Path(p).name
    return (
        "/test" in p
        or p.startswith("test")
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("_test.go")
        or ".test." in name
        or name.endswith("_spec.js")
        or name.endswith("_test.rb")
    )


def _path_related(path: str, known: Set[str]) -> bool:
    stem = Path(path).stem.lower().replace("test_", "").replace("_test", "")
    for k in known:
        ks = Path(k).stem.lower().replace("test_", "").replace("_test", "")
        if stem and (stem in ks or ks in stem):
            return True
    return False


def _task_mentions_tests(task: str) -> bool:
    return bool(re.search(r"\b(tests?|pytest|unit tests|add tests|update tests)\b", task or "", re.I))
