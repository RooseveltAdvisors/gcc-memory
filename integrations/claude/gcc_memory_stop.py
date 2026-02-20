#!/usr/bin/env python3
"""Claude Stop hook that captures assistant responses as 'thought' events."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Allow importing hook_common from the same directory
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hook_common import load_gcc_memory, ensure_branch, is_debounced, trim


def main() -> int:
    ensure_project_store, ensure_global_store, Event = load_gcc_memory()
    if Event is None:
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    last_message = payload.get("last_assistant_message") or ""
    stripped = last_message.strip()
    if not stripped:
        return 0
    # Skip terse responses that add noise (e.g. "Done.", "Implemented.", "OK")
    if len(stripped) < 60:
        return 0

    cwd = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    ensure_branch(cwd)
    store = ensure_project_store(cwd)
    ensure_global_store()
    branch = store.get_active_branch()
    agent_name = os.environ.get("GCC_MEMORY_AGENT", "claude")

    if is_debounced(store, branch, agent_name, "thought"):
        return 0

    # Truncate to a meaningful summary (first non-empty line)
    lines = [l.strip() for l in last_message.strip().splitlines() if l.strip()]
    summary = lines[0][:200] if lines else "assistant response"

    event = Event(
        agent=agent_name,
        channel="claude-hook",
        tags=("thought", "response"),
        summary=summary,
        details=trim(last_message),
        thought=trim(last_message),
    )
    store.append_event(branch, event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
