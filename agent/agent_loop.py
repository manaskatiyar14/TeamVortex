"""Core agent orchestration: analyze → explore → plan → edit → test → recover → verify."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
from agent import json_util
from agent import tools
from agent import ui
from agent.baseline import capture_baseline
from agent.context import (
    UNTRUSTED_DATA_NOTICE,
    AgentContext,
    AttemptRecord,
    TaskType,
    TestResult,
    VerificationStatus,
)
from agent.failure import count_failing_tests, failing_test_progress
from agent.llm_client import generate_text
from agent.planner import create_plan, minimal_change_rules
from agent.recovery import (
    analyze_failure,
    capture_failure_evidence,
    expand_context_from_output,
    summarize_test_failure,
)
from agent.repo_map import build_repo_map, repo_map_prompt_block
from agent.task_analyzer import analyze_task, extract_issue_signals, summarize_task
from agent.test_framework import detect_test_framework, run_detected_tests, run_targeted_tests
from agent.verifier import verify


def run_agent(
    repo_path: str | Path,
    task: str,
    *,
    test_path: Optional[str | Path] = None,
    task_type: Optional[TaskType] = None,
    provided_tests: str = "",
    cleanup_workspace: bool = False,
) -> bool:
    """Run the coding agent against a workspace. Returns True if VERIFIED (or tests pass when inconclusive git)."""
    tools.clear_file_cache()
    root = tools.set_repo_root(repo_path)
    task = (task or "").strip()
    if not task:
        print("ERROR: Task/issue description is empty.")
        return False

    resolved_type = task_type or analyze_task(task, has_repo=True)
    signals = extract_issue_signals(task)
    ctx = AgentContext(
        task=task,
        task_type=resolved_type,
        workspace=str(root),
        max_attempts=config.MAX_RETRY_ATTEMPTS,
        provided_tests=provided_tests,
        workspace_cleanup=cleanup_workspace,
        issue_signals=signals,
        issue_kind=(
            "STANDALONE" if resolved_type == TaskType.STANDALONE else str(signals.get("issue_kind") or "UNKNOWN")
        ),
        acceptance_criteria=list(signals.get("acceptance_criteria") or []),
    )

    ui.banner()
    mode_label = "Standalone Coding Problem" if resolved_type == TaskType.STANDALONE else "Repository Engineering Task"
    ui.task_overview(
        task=task,
        mode=f"{mode_label} [{ctx.issue_kind}]",
        workspace=str(root),
        model=f"{config.LLM_PROVIDER}/{config.MODEL_NAME} (temp={config.TEMPERATURE})",
        max_attempts=ctx.max_attempts,
    )
    ui.stage("ANALYZE", summarize_task(task, resolved_type, issue_kind=ctx.issue_kind))

    try:
        return _run_loop(ctx, test_path=test_path)
    finally:
        if cleanup_workspace:
            from agent.standalone import cleanup_workspace as _cleanup

            _cleanup(Path(root))


def _run_loop(ctx: AgentContext, *, test_path: Optional[str | Path]) -> bool:
    # --- Explore ---
    ui.stage("EXPLORE", "Exploring repository metadata and relevant code.")
    metadata = tools.detect_project_metadata()
    ctx.repository_metadata = metadata
    ctx.project_type = str(metadata.get("project_type") or "unknown")

    detection = detect_test_framework(metadata)
    ctx.test_command = detection.get("command")
    ui.info(f"project_type={ctx.project_type} test_framework={detection.get('framework')}")

    all_files = tools.list_files()
    if not all_files:
        print("ERROR: Empty repository / workspace — no files found.")
        ctx.verification_status = VerificationStatus.FAILED
        ui.result("FAILED", "Empty workspace.")
        return False

    ctx.baseline = capture_baseline()
    ctx.repo_map = build_repo_map(all_files, metadata)
    ui.stage(
        "EXPLORE",
        f"Repository map: {ctx.repo_map.get('file_count')} file(s), "
        f"langs={ctx.repo_map.get('languages')}",
    )

    listing_limit = int(getattr(config, "MAX_FILE_LISTING_CHARS", 8000))
    file_listing = "\n".join(all_files)
    if len(file_listing) > listing_limit:
        file_listing = file_listing[:listing_limit] + "\n... (listing truncated)"

    search_hits = _initial_search(ctx)
    ui.stage("SEARCH", f"Located {len(search_hits)} search hit(s).")

    relevant = _select_relevant_files(ctx, file_listing, search_hits)
    related_tests: List[str] = []
    for path in list(relevant):
        if "test" not in path.lower():
            related_tests.extend(tools.find_related_tests(path))
    for tpath in related_tests:
        if tpath not in relevant:
            relevant.append(tpath)
    ctx.related_tests = related_tests[:20]

    ui.stage("READ", f"Reading context for {len(relevant)} file(s).")
    for path in relevant[: config.MAX_CONTEXT_FILES]:
        try:
            ctx.mark_read(path, tools.read_file(path))
        except (OSError, PermissionError, UnicodeError) as exc:
            ui.info(f"skip unreadable {path}: {exc}")

    _maybe_capture_baseline_tests(ctx, detection, test_path)

    # --- Plan ---
    ui.stage("PLAN", "Creating repair / implementation strategy.")
    hits_text = "\n".join(
        f"{h.get('path')}:{h.get('line')}: {h.get('text')}" for h in search_hits[:30]
    )
    plan = create_plan(ctx, file_listing=file_listing, search_hits=hits_text)
    ui.info(plan.to_prompt_block())

    for path in plan.files_to_inspect + plan.tests_to_inspect:
        if path in all_files and path not in ctx.files_read:
            try:
                ctx.mark_read(path, tools.read_file(path))
            except (OSError, PermissionError, UnicodeError):
                continue

    # --- Edit / test / recover loop ---
    passed = False
    no_tests = not ctx.test_command and test_path is None
    previous_fail_count = (
        count_failing_tests(
            (ctx.baseline_test.stdout or "") + "\n" + (ctx.baseline_test.stderr or "")
        )
        if ctx.baseline_test
        else -1
    )

    for attempt in range(1, ctx.max_attempts + 1):
        ctx.attempt = attempt
        if ctx.model_calls >= int(getattr(config, "MAX_MODEL_CALLS", 30)):
            ui.info("Resource budget exhausted: MAX_MODEL_CALLS.")
            ctx.record_failure("Aborted: model-call budget exhausted.")
            break
        if ctx.no_progress:
            streak = int(getattr(config, "NO_PROGRESS_ABORT_STREAK", 3))
            fps = ctx.failure_fingerprints
            if len(fps) >= streak and len(set(fps[-streak:])) == 1:
                ui.stage("RECOVER", "No-progress streak — aborting honestly.")
                ctx.record_failure("Aborted: identical failure repeated with no new evidence.")
                break

        ui.stage("EDIT", f"Attempt {attempt}/{ctx.max_attempts}")

        operations = _generate_fix(ctx, file_listing=file_listing)
        if not operations:
            note = "LLM returned no file patches."
            ui.info(note)
            ctx.record_failure(f"Attempt {attempt}: {note} Do not return an empty patch list.")
            continue

        applied = tools.apply_file_operations(operations)
        written = list(applied.get("applied") or [])
        if not applied.get("ok"):
            ui.info(f"edit rejected: {applied.get('error')}")
            ctx.record_failure(
                f"Attempt {attempt}: edit rejected ({applied.get('error')}). "
                "Produce a valid in-workspace patch with a real change."
            )
            continue

        for rel_path in written:
            try:
                ctx.mark_modified(rel_path, tools.read_file(rel_path))
            except (OSError, PermissionError, UnicodeError):
                ctx.mark_modified(rel_path, "")
            ui.stage("EDIT", f"{rel_path} modified.")
            if ctx.baseline is not None:
                ctx.baseline.record_vortex_write(rel_path)

        fp = applied.get("fingerprint") or ""
        if fp:
            if fp in ctx.edit_fingerprints:
                ctx.no_progress = True
                ui.info("[RECOVER] Identical edit repeated.")
            ctx.edit_fingerprints.append(fp)

        if not written:
            ctx.record_failure(f"Attempt {attempt}: no-op edit (content unchanged).")
            continue

        ui.stage("TEST", "Running verification.")
        if no_tests:
            result = TestResult(
                passed=True,
                exit_code=0,
                stdout="",
                stderr="No testing framework confidently detected.",
                command="",
                targeted=False,
                test_kind="none",
            )
            ctx.test_results.append(result)
            ctx.no_tests_detected = True
            ui.stage("TEST", "No tests available — verification will be INCONCLUSIVE.")
            passed = True
            break

        code, stdout, stderr, command = _run_tests_for_attempt(ctx, test_path)
        ctx.tests_executed += 1
        result = TestResult(
            passed=(code == 0),
            exit_code=code,
            stdout=stdout,
            stderr=stderr,
            command=command,
            targeted=bool(test_path or (ctx.plan and ctx.plan.tests_to_inspect)),
            test_kind="provided" if ctx.provided_tests else "repository",
        )
        ctx.test_results.append(result)
        summary = result.summary(max_chars=config.MAX_COMMAND_OUTPUT_CHARS)
        fail_count = count_failing_tests(stdout + "\n" + stderr)
        progress = failing_test_progress(previous_fail_count, fail_count)

        record = AttemptRecord(
            attempt=attempt,
            hypothesis=ctx.hypothesis,
            files_changed=list(written),
            tests_run=command,
            failure_signature="",
            new_information=f"failing_tests={fail_count} progress={progress}",
            result="passed" if code == 0 else "failed",
            failing_test_count=fail_count if fail_count >= 0 else 0,
        )

        if code == 0:
            ui.stage("TEST", "Tests passed.")
            if result.targeted and ctx.test_command and test_path is None:
                b_code, b_out, b_err, b_cmd = run_detected_tests(detection=detection)
                ctx.tests_executed += 1
                broader = TestResult(
                    passed=(b_code == 0),
                    exit_code=b_code,
                    stdout=b_out,
                    stderr=b_err,
                    command=b_cmd,
                    targeted=False,
                    test_kind=result.test_kind,
                )
                ctx.test_results.append(broader)
                if b_code != 0:
                    ui.stage("TEST", "Broader suite failed.")
                    structured = summarize_test_failure(
                        ctx,
                        exit_code=b_code,
                        stdout=b_out,
                        stderr=b_err,
                        command=b_cmd,
                    )
                    record.failure_signature = structured.fingerprint()
                    record.result = "broader_failed"
                    ctx.attempt_history.append(record)
                    evidence = capture_failure_evidence(ctx, broader.summary())
                    ui.stage("RECOVER", "Analyzing failure...")
                    analyze_failure(ctx, evidence, structured=structured)
                    expand_context_from_output(ctx, broader.summary(), all_files)
                    previous_fail_count = count_failing_tests(b_out + "\n" + b_err)
                    continue
                ui.stage("TEST", "Broader suite passed.")
            ctx.attempt_history.append(record)
            passed = True
            break

        ui.stage("TEST", f"Tests failed (failing≈{fail_count}, {progress}).")
        structured = summarize_test_failure(
            ctx,
            exit_code=code,
            stdout=stdout,
            stderr=stderr,
            command=command,
        )
        record.failure_signature = structured.fingerprint()
        ctx.attempt_history.append(record)
        evidence = capture_failure_evidence(ctx, summary)
        ui.stage("RECOVER", "Analyzing failure... Updating hypothesis.")
        analyze_failure(ctx, evidence, structured=structured)
        expand_context_from_output(ctx, summary, all_files)
        if progress == "improved":
            ui.info("Partial progress: fewer failing tests than the previous attempt.")
        elif progress == "worsened":
            ui.info("Regression vs previous attempt: more failing tests.")
        previous_fail_count = fail_count if fail_count >= 0 else previous_fail_count

    if not passed:
        ui.result(
            "FAILED",
            f"Tests did not pass after {ctx.max_attempts} attempt(s). "
            f"{ctx.metrics_summary()}",
        )
        if ctx.previous_failures:
            ui.info(ctx.previous_failures[-1][:2000])
        ctx.verification_status = VerificationStatus.FAILED
        return False

    # --- Verify ---
    ui.stage("VERIFY", "Inspecting diff, baseline, and acceptance criteria.")
    status = verify(ctx)
    ws_path = Path(ctx.workspace)
    sol_file = (ws_path / "solution.py") if (ws_path / "solution.py").is_file() else None
    test_file = (ws_path / "test_solution.py") if (ws_path / "test_solution.py").is_file() else None

    ui.result(
        status.value,
        ctx.verification_report,
        workspace=str(ws_path),
        solution_path=str(sol_file) if sol_file else None,
        test_path=str(test_file) if test_file else None,
        files_changed=list(ctx.files_modified),
        metrics=ctx.metrics_summary(),
    )
    return status in (VerificationStatus.VERIFIED, VerificationStatus.INCONCLUSIVE)


def _maybe_capture_baseline_tests(ctx: AgentContext, detection: Dict, test_path: Optional[str | Path]) -> None:
    """Cheap before-state of tests when the repo is modest and a command exists."""
    file_count = int((ctx.repo_map or {}).get("file_count") or 0)
    threshold = int(getattr(config, "LARGE_REPO_FILE_THRESHOLD", 200))
    if file_count > threshold:
        return
    if test_path is None and not ctx.test_command:
        return
    try:
        code, out, err, cmd = _run_tests_for_attempt(ctx, test_path)
    except Exception as exc:
        ui.info(f"baseline tests skipped: {exc}")
        return
    ctx.tests_executed += 1
    ctx.baseline_test = TestResult(
        passed=(code == 0),
        exit_code=code,
        stdout=out,
        stderr=err,
        command=cmd,
        targeted=bool(test_path),
        test_kind="baseline",
    )
    ui.info(
        f"baseline tests: {'passed' if code == 0 else 'failed'} "
        f"(failing≈{count_failing_tests(out + chr(10) + err)})"
    )


def _initial_search(ctx: AgentContext) -> List[Dict]:
    import re

    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", ctx.task)
    stop = {
        "the", "and", "for", "that", "with", "this", "from", "failing", "tests",
        "fix", "bug", "issue", "implement", "feature", "function", "return",
        "should", "must", "when", "then", "into", "about",
    }
    keywords = [t for t in tokens if t.lower() not in stop][:8]
    signals = ctx.issue_signals or {}
    for extra in (signals.get("quoted") or []) + (signals.get("symbols") or []):
        if isinstance(extra, str) and extra not in keywords:
            keywords.append(extra)
    keywords = keywords[:12]

    hits: List[Dict] = []
    seen = set()
    search_budget = int(getattr(config, "MAX_SEARCH_QUERIES", 40))
    for kw in keywords:
        if ctx.search_count >= search_budget:
            break
        ctx.bump_search()
        for hit in tools.search_code(kw, max_results=8):
            key = (hit.get("path"), hit.get("line"))
            if key in seen:
                continue
            seen.add(key)
            hits.append(hit)

    for path in signals.get("paths") or []:
        if isinstance(path, str) and path not in {h.get("path") for h in hits}:
            hits.append({"path": path, "line": 0, "text": "(mentioned in issue)"})
    return hits[: config.MAX_SEARCH_RESULTS]


def _select_relevant_files(
    ctx: AgentContext,
    file_listing: str,
    search_hits: List[Dict],
) -> List[str]:
    hit_paths = []
    for h in search_hits:
        p = h.get("path")
        if isinstance(p, str) and p not in hit_paths:
            hit_paths.append(p)

    prompt = (
        "You are a coding agent. Given a task, file listing, and search hits, "
        "return JSON only.\n"
        'Schema: {"files": ["relative/path.py", ...]}\n'
        "Pick the smallest set of implementation AND test files needed. "
        "Do not invent paths that are not in the listing.\n"
        f"{minimal_change_rules()}\n\n"
        f"{UNTRUSTED_DATA_NOTICE}\n\n"
        f"TASK:\n{ctx.task}\n\n"
        f"ISSUE KIND: {ctx.issue_kind}\n"
        f"ACCEPTANCE CRITERIA: {ctx.acceptance_criteria or '(none explicit)'}\n"
        f"SEARCH HIT PATHS:\n{hit_paths}\n\n"
        f"FILES:\n{file_listing[:8000]}\n"
    )
    ctx.bump_model_call()
    raw = generate_text(prompt, json_mode=True)
    data = json_util.parse_json_object(raw)
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        ctx.bump_model_call()
        raw = generate_text(
            prompt
            + "\n\nYour previous reply was not valid JSON with a files array. "
            'Return only {"files": ["relative/path", ...]} using paths from FILES.',
            json_mode=True,
        )
        data = json_util.parse_json_object(raw)
        files = data.get("files") if isinstance(data, dict) else None

    listing_set = set(line for line in file_listing.splitlines() if line and not line.startswith("..."))
    selected: List[str] = []
    if isinstance(files, list):
        for p in files:
            if isinstance(p, str) and p in listing_set and p not in selected:
                selected.append(p)

    if not selected:
        selected = [p for p in hit_paths if p in listing_set][:10]

    ctx.relevant_files = selected
    return selected


def _generate_fix(ctx: AgentContext, *, file_listing: str) -> List[Dict]:
    plan_block = ctx.plan.to_prompt_block() if ctx.plan else "(none)"
    if ctx.task_type == TaskType.STANDALONE:
        if not ctx.provided_tests.strip():
            mode_rules = (
                "- For this standalone coding problem: author the complete implementation in 'solution.py' "
                "AND author comprehensive pytest unit tests in 'test_solution.py' "
                "(covering normal cases, edge cases, and problem constraints).\n"
                "- Return BOTH files in the 'files' array.\n"
            )
        else:
            mode_rules = (
                "- For this standalone coding problem: implement the required logic in 'solution.py' "
                "so that all tests in 'test_solution.py' pass. Do not modify 'test_solution.py'.\n"
            )
    else:
        mode_rules = "- Prefer editing implementation files; do not modify tests unless the task requires it.\n"

    prompt = (
        "You are a text-only coding agent. Produce a minimal fix. "
        "Return JSON only, no markdown.\n"
        "Schema: {\"files\": [{\"path\": \"relative/path\", \"content\": \"full file text\"}]}\n"
        "You MAY instead use a targeted replacement: "
        "{\"path\": \"relative/path\", \"old\": \"exact unique snippet\", \"new\": \"replacement\"}.\n"
        f"{minimal_change_rules()}\n"
        "Rules:\n"
        "- Only modify files that must change.\n"
        f"{mode_rules}"
        "- If tests failed, use the error output and do NOT repeat the previous failing approach.\n"
        "- Do not add unrelated validation or features.\n"
        f"- This is attempt {ctx.attempt} of {ctx.max_attempts}.\n"
        f"- Current hypothesis: {ctx.hypothesis or '(none)'}\n\n"
        f"{UNTRUSTED_DATA_NOTICE}\n\n"
        f"TASK:\n{ctx.task}\n\n"
        f"PLAN:\n{plan_block}\n\n"
        f"RECOVERY BRIEF:\n{ctx.recovery_brief()}\n\n"
        f"REPO MAP:\n{repo_map_prompt_block(ctx.repo_map) if ctx.repo_map else '(none)'}\n\n"
        f"REPOSITORY FILES (listing may be truncated):\n{file_listing[:4000]}\n\n"
        f"CURRENT FILE CONTENTS:\n{ctx.files_block(config.MAX_FILE_CHARS, config.MAX_CONTEXT_FILES)}\n\n"
        f"PREVIOUS ATTEMPTS (avoid repeating these):\n{ctx.history_block()}\n\n"
        f"LAST TEST OUTPUT:\n"
        f"{(ctx.test_results[-1].summary() if ctx.test_results else '(not run yet)')}\n"
    )
    ctx.bump_model_call()
    raw = generate_text(prompt, json_mode=True)
    data = json_util.parse_json_object(raw)
    items = data.get("files") if isinstance(data, dict) else None
    if not isinstance(items, list):
        ctx.bump_model_call()
        raw = generate_text(
            prompt
            + "\n\nYour previous reply was not valid JSON matching the schema. "
            'Return only {"files": [{"path": "...", "content": "..."}]}.'
            ,
            json_mode=True,
        )
        data = json_util.parse_json_object(raw)
        items = data.get("files") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []

    operations: List[Dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        path = path.strip()
        old, new = item.get("old"), item.get("new")
        content = item.get("content")
        if isinstance(old, str) and isinstance(new, str):
            operations.append({"path": path, "old": old, "new": new})
        elif isinstance(content, str):
            operations.append({"path": path, "content": content})
    return operations


def _run_tests_for_attempt(
    ctx: AgentContext,
    test_path: Optional[str | Path],
) -> Tuple[int, str, str, str]:
    if test_path is not None:
        return run_detected_tests(test_path=test_path)

    test_files: List[str] = []
    if ctx.plan:
        test_files = list(ctx.plan.tests_to_inspect)
    if not test_files:
        test_files = [p for p in ctx.relevant_files if "test" in p.lower()]

    py_tests = [p for p in test_files if p.endswith(".py")]
    if py_tests and ctx.project_type in ("python", "unknown"):
        return run_targeted_tests(py_tests)

    return run_detected_tests()
