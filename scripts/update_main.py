#!/usr/bin/env python3
"""Refresh .gcc/main.md — only regenerates the auto-updated section below the marker."""

from __future__ import annotations

import argparse
from pathlib import Path

from gcc_memory.agent_runtime import ensure_project_store

MARKER = "<!-- AUTO-UPDATED BELOW - Do not edit below this line -->"


_NOISE_SUMMARIES = frozenset({
    "committed.", "done.", "implemented.", "ok", "ok.", "yes", "no",
    "continue", "commit", "test", "error", "fixed.", "merged.",
})


def build_highlights(events: list[dict], target: int = 5) -> str:
    if not events:
        return "(no recent events)"
    lines = []
    for event in reversed(events):
        summary = event.get("summary") or ""
        # Skip terse/noisy entries
        if len(summary.strip()) < 15 or summary.strip().lower() in _NOISE_SUMMARIES:
            continue
        stamp = event.get("timestamp", "")
        agent = event.get("agent", "agent")
        lines.append(f"- [{stamp}] {agent}: {summary}")
        if len(lines) >= target:
            break
    return "\n".join(lines) if lines else "(no substantive recent events)"


def main() -> None:
    parser = argparse.ArgumentParser(description="Update .gcc/main.md with recent highlights")
    parser.add_argument("workspace", nargs="?", default=".", help="Workspace path (defaults to cwd)")
    parser.add_argument("--branch", help="Branch to summarise (defaults to active branch)")
    parser.add_argument("--limit", type=int, default=5, help="Number of events to include")
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    store = ensure_project_store(workspace)
    branch = args.branch or store.get_active_branch()
    # Fetch extra events so we can filter noise and still get enough highlights
    events = store.recent_events(branch, limit=args.limit * 4)
    highlights = build_highlights(events, target=args.limit)

    branches_list = ", ".join(store.list_branches()) or "main"

    auto_section = (
        f"{MARKER}\n"
        f"## Status\n"
        f"- Active branch: {branch}\n"
        f"- Branches: {branches_list}\n"
        f"\n"
        f"## Recent Highlights\n"
        f"{highlights}\n"
    )

    main_path = store.root / "main.md"
    if main_path.exists():
        current = main_path.read_text(encoding="utf-8")
    else:
        current = ""

    if MARKER in current:
        # Preserve everything above the marker
        curated = current.split(MARKER)[0]
        new_content = curated + auto_section
    else:
        # Legacy file without marker — append marker before auto content
        new_content = current.rstrip() + "\n\n" + auto_section

    if current.strip() == new_content.strip():
        return
    main_path.write_text(new_content, encoding="utf-8")


if __name__ == "__main__":
    main()
