"""Failure recovery: structured evidence, iterative retrieval, no-progress detection."""

from __future__ import annotations

from typing import List

import config
from agent.context import UNTRUSTED_DATA_NOTICE, AgentContext
from agent import json_util
from agent.failure import FailureSummary, detect_no_progress, parse_failure_output
from agent.llm_client import generate_text
from agent.planner import minimal_change_rules, update_plan
from agent.repo_map import extract_imports, resolve_import_to_files
from agent import tools
from agent import ui


def capture_failure_evidence(ctx: AgentContext, test_summary: str) -> str:
    """Build a compact failure evidence blob for recovery (backward compatible)."""
    return (
        f"Attempt {ctx.attempt}/{ctx.max_attempts}\n"
        f"Hypothesis: {ctx.hypothesis or '(none)'}\n"
        f"Files modified: {ctx.files_modified}\n"
        f"Test output:\n{test_summary}"
    )


def summarize_test_failure(
    ctx: AgentContext,
    *,
    exit_code: int,
    stdout: str,
    stderr: str,
    command: str,
) -> FailureSummary:
    """Parse failure output into structured evidence and record fingerprint."""
    summary = parse_failure_output(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        command=command,
    )
    fp = summary.fingerprint()
    ctx.failure_fingerprints.append(fp)
    if detect_no_progress(ctx.failure_fingerprints, threshold=2):
        ctx.no_progress = True
        ui.info("[RECOVER] No-progress detected — identical failure repeating.")
    return summary


def analyze_failure(ctx: AgentContext, evidence: str, *, structured: FailureSummary | None = None) -> str:
    """Ask the model what went wrong and what different approach to try next."""
    structured_block = structured.to_prompt_block() if structured else "(none)"
    strategy_note = ""
    if ctx.no_progress:
        strategy_note = (
            "CRITICAL: Identical failures are repeating. You MUST change strategy "
            "(different file, different root cause, or revert previous edit approach).\n"
        )

    prompt = (
        "A coding-agent attempt failed tests. Analyze the failure and return JSON only.\n"
        "Schema: {\n"
        '  "analysis": "what failed and why",\n'
        '  "new_hypothesis": "different approach to try",\n'
        '  "files_to_reread": ["path", ...],\n'
        '  "symbols_to_search": ["symbol", ...],\n'
        '  "next_action": "inspect_file|search_symbol|edit|replan",\n'
        '  "reason_different_approach_needed": "string"\n'
        "}\n"
        f"{minimal_change_rules()}\n"
        f"{UNTRUSTED_DATA_NOTICE}\n"
        f"{strategy_note}"
        f"TASK:\n{ctx.task}\n\n"
        f"RECOVERY BRIEF:\n{ctx.recovery_brief()}\n\n"
        f"CURRENT PLAN:\n{(ctx.plan.to_prompt_block() if ctx.plan else '(none)')}\n\n"
        f"STRUCTURED FAILURE:\n{structured_block}\n\n"
        f"EVIDENCE:\n{evidence[:4000]}\n\n"
        f"CURRENT CODE:\n{ctx.files_block(max_file_chars=config.MAX_FILE_CHARS, max_files=config.MAX_CONTEXT_FILES)}\n"
        f"EDIT FINGERPRINTS (do not repeat): {ctx.edit_fingerprints[-5:]}\n"
    )
    ctx.bump_model_call()
    raw = generate_text(prompt, json_mode=True)
    data = json_util.parse_json_object(raw)
    analysis = str(data.get("analysis") or "Tests failed; approach must change.")
    new_hyp = str(data.get("new_hypothesis") or "")
    if new_hyp:
        ctx.hypothesis = new_hyp

    # Iterative retrieval from model guidance + structured failure paths
    files_to_reread = data.get("files_to_reread") if isinstance(data.get("files_to_reread"), list) else []
    for path in list(files_to_reread) + list((structured.traceback_files if structured else [])[:5]):
        if isinstance(path, str) and path.strip():
            _safe_reread(ctx, path.strip())

    symbols = data.get("symbols_to_search") if isinstance(data.get("symbols_to_search"), list) else []
    for sym in symbols[:5]:
        if not isinstance(sym, str):
            continue
        ctx.bump_search()
        for hit in tools.find_symbol(sym, max_results=8):
            p = hit.get("path")
            if isinstance(p, str):
                _safe_reread(ctx, p)

    # Follow imports from recently read implementation files
    iterative_retrieve_from_reads(ctx)

    reason = str(data.get("reason_different_approach_needed") or analysis)
    next_action = str(data.get("next_action") or "edit")
    note = (
        f"Attempt {ctx.attempt} failed.\n"
        f"Analysis: {analysis}\n"
        f"New hypothesis: {ctx.hypothesis}\n"
        f"Next action: {next_action}\n"
        f"Why a different approach is needed: {reason}\n"
        f"Do not repeat the previous modification."
    )
    if ctx.no_progress:
        note += "\nNO-PROGRESS: change strategy; do not emit the same edit again."
    ctx.record_failure(note)
    update_plan(ctx, failure_evidence=evidence + "\n" + structured_block)
    return note


def expand_context_from_output(ctx: AgentContext, output: str, known_files: List[str]) -> None:
    """Pull a few extra files mentioned in test output into context."""
    added = 0
    for rel in known_files:
        if added >= 5:
            break
        if rel and rel in output:
            if _safe_reread(ctx, rel):
                added += 1
    # Also parse paths from structured failure if present in output
    summary = parse_failure_output(exit_code=1, stdout=output, stderr="")
    for path in summary.traceback_files[:5]:
        # Try exact and basename matches against known files
        if path in known_files:
            _safe_reread(ctx, path)
            continue
        base = path.split("/")[-1]
        for rel in known_files:
            if rel.endswith(base):
                _safe_reread(ctx, rel)
                break


def iterative_retrieve_from_reads(ctx: AgentContext, *, max_new: int = 4) -> int:
    """Discover imports/symbols from already-read files and pull additional context."""
    all_files = tools.list_files()
    added = 0
    for path, content in list(ctx.files_read.items()):
        if added >= max_new:
            break
        for imp in extract_imports(content)[:8]:
            for candidate in resolve_import_to_files(imp, all_files):
                if candidate in ctx.files_read:
                    continue
                if _safe_reread(ctx, candidate):
                    added += 1
                    if added >= max_new:
                        return added
    return added


def _safe_reread(ctx: AgentContext, path: str) -> bool:
    # Normalize accidental absolute/truncated traceback paths
    candidates = [path]
    if "/" in path:
        candidates.append(path.split("/")[-1])
    all_files = None
    for cand in candidates:
        try:
            tools.invalidate_cache(cand)
            content = tools.read_file(cand)
            ctx.mark_read(cand, content)
            return True
        except (OSError, PermissionError, UnicodeError, RuntimeError):
            if all_files is None:
                try:
                    all_files = tools.list_files()
                except Exception:
                    all_files = []
            for rel in all_files or []:
                if rel.endswith(cand) or rel.endswith("/" + cand):
                    try:
                        tools.invalidate_cache(rel)
                        content = tools.read_file(rel)
                        ctx.mark_read(rel, content)
                        return True
                    except (OSError, PermissionError, UnicodeError):
                        continue
    return False
