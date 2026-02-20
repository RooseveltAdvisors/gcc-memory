#!/usr/bin/env python3
"""Claude UserPromptSubmit hook that captures user prompts as 'observation' events."""

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

    user_prompt = payload.get("user_prompt") or ""
    if not user_prompt.strip():
        return 0

    cwd = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    ensure_branch(cwd)
    store = ensure_project_store(cwd)
    ensure_global_store()
    branch = store.get_active_branch()
    agent_name = os.environ.get("GCC_MEMORY_AGENT", "claude")

    if is_debounced(store, branch, agent_name, "observation"):
        return 0

    # Summary: first line of the prompt, truncated
    lines = [l.strip() for l in user_prompt.strip().splitlines() if l.strip()]
    summary = lines[0][:200] if lines else "user prompt"

    event = Event(
        agent=agent_name,
        channel="claude-hook",
        tags=("observation", "prompt"),
        summary=summary,
        details=trim(user_prompt),
        observation=trim(user_prompt),
    )
    store.append_event(branch, event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
