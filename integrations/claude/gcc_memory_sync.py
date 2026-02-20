#!/usr/bin/env python3
"""Claude PostToolUse hook that mirrors tool output into gcc-memory."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Allow importing hook_common from the same directory
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hook_common import (
    coerce_text as _coerce_text,
    trim as _trim,
    resolve_repo_root as _resolve_repo_root,
    script_path as _script_path,
    run_script as _run_script,
    load_gcc_memory as _load_gcc_memory,
    ensure_branch as _ensure_branch,
    is_debounced as _is_debounced,
)

ALLOWED_TOOLS = {
    "bash",
    "write",
    "edit",
    "applypatch",
    "plan",
    "task",
    "multiedit",
}
# Read-only tools that don't change state — skip these
READONLY_TOOLS = {
    "read",
    "glob",
    "grep",
    "websearch",
    "webfetch",
    "listresources",
    "readresource",
}

AUTO_COMMIT_COOLDOWN = 300  # Auto-commit every 5 minutes of activity


def _run_update_main(cwd: Path, branch: str) -> None:
    script = _script_path("scripts/update_main.py")
    args = [str(cwd), "--branch", branch]
    _run_script(script, args)


def _maybe_auto_commit(store, branch: str) -> None:
    """Auto-commit progress every AUTO_COMMIT_COOLDOWN seconds of activity."""
    marker_dir = store.root / ".autocommit"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / f"{branch}.last"
    now = time.time()
    if marker.exists():
        try:
            last = marker.stat().st_mtime
            if now - last < AUTO_COMMIT_COOLDOWN:
                return
        except OSError:
            pass
    # Check that there are events to commit
    events = store.recent_events(branch, 5)
    if not events:
        return
    try:
        store.commit(branch, "Auto checkpoint", include_last=10)
        store.update_metadata()
        marker.write_text(str(int(now)), encoding="utf-8")
    except Exception as exc:
        print(f"hook-warning: auto-commit failed ({exc})", file=sys.stderr)


def _build_enriched_summary(tool_name: str, payload: dict) -> str:
    """Build a descriptive summary instead of just echoing the command."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return f"{tool_name} executed"

    if tool_name == "bash":
        desc = tool_input.get("description", "")
        cmd = tool_input.get("command", "")
        cmd_short = cmd.strip().splitlines()[0][:120] if cmd else ""
        result_obj = payload.get("tool_result") or {}
        exit_code = ""
        if isinstance(result_obj, dict):
            exit_code = result_obj.get("exit_code") or result_obj.get("returncode") or ""
        base = desc or cmd_short or "bash command"
        if exit_code and str(exit_code) != "0":
            return f"{base} (exit {exit_code})"
        return base

    if tool_name in ("write", "edit", "multiedit", "applypatch"):
        file_path = tool_input.get("file_path") or tool_input.get("path") or ""
        if file_path:
            short_path = file_path.split("/")[-1] if "/" in str(file_path) else file_path
            return f"{tool_name}: {short_path}"
        return f"{tool_name} executed"

    if tool_name == "task":
        desc = tool_input.get("description", "")
        return f"task: {desc}" if desc else "task delegated"

    if tool_name == "plan":
        return "entered plan mode"

    return f"{tool_name} executed"


def main() -> int:
    ensure_project_store, ensure_global_store, Event = _load_gcc_memory()
    if Event is None:
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:  # pragma: no cover - malformed hook payloads
        return 0

    tool_name = (payload.get("tool_name") or "").lower()

    # Skip read-only tools that don't change state
    if tool_name in READONLY_TOOLS:
        return 0
    # Only allow known state-changing tools
    if tool_name and tool_name not in ALLOWED_TOOLS:
        return 0

    # Skip failed or empty operations
    result_obj = payload.get("tool_result") or {}
    if isinstance(result_obj, dict) and result_obj.get("error"):
        return 0

    cwd = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    _ensure_branch(cwd)
    store = ensure_project_store(cwd)
    ensure_global_store()
    branch = store.get_active_branch()
    agent_name = os.environ.get("GCC_MEMORY_AGENT", "claude")

    # Debounce rapid-fire events from same agent+tool
    if _is_debounced(store, branch, agent_name, tool_name):
        return 0

    summary = _build_enriched_summary(tool_name, payload)

    # Build lean details — only include if there's real content
    detail_parts: list[str] = []

    file_path = ""
    if isinstance(payload.get("tool_input"), dict):
        file_path = payload["tool_input"].get("file_path") or payload["tool_input"].get("path") or ""
    if file_path:
        detail_parts.append(file_path)

    output = None
    if isinstance(result_obj, dict):
        output = result_obj.get("stdout") or result_obj.get("output") or result_obj.get("content")
    if not output and isinstance(result_obj, str):
        output = result_obj
    if output:
        output_text = _trim(_coerce_text(output))
        if output_text and output_text not in ("{}", "null", "''", '""', "None") and len(output_text) < 2000:
            detail_parts.append(output_text)

    details = "\n".join(detail_parts)

    event = Event(
        agent=agent_name,
        channel="claude-hook",
        tags=(tool_name or "tool",),
        summary=summary,
        details=details,
        action=summary,
    )
    store.append_event(branch, event)
    _maybe_auto_commit(store, branch)
    _run_update_main(cwd, branch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
