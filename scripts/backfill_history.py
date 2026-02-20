#!/usr/bin/env python3
"""Backfill existing Claude/Codex histories into gcc-memory.

Primary source: Claude session transcripts (~/.claude/projects/<project>/*.jsonl)
which contain full OTA data — agent reasoning, tool calls, and file changes.

Fallback source: history.jsonl files (user prompts only, from Codex or older Claude).

Creates per-session events with rich summaries grouped into daily commits.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Set, Tuple

from gcc_memory.agent_runtime import ensure_global_store, ensure_project_store
from gcc_memory.store import Event

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def load_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def build_existing_keys(store, branch: str) -> Set[Tuple[str, str, str]]:
    existing: Set[Tuple[str, str, str]] = set()
    for record in store.iter_events(branch):
        key = (
            record.get("agent", ""),
            record.get("channel", ""),
            record.get("summary", ""),
        )
        existing.add(key)
    return existing


def _date_key(timestamp: str | None) -> str:
    if not timestamp:
        return "unknown"
    return timestamp[:10]


def _fmt_ts(ts) -> str | None:
    """Convert various timestamp formats to ISO."""
    if not ts:
        return None
    if isinstance(ts, str):
        # Already ISO-ish
        if "T" in ts:
            return ts[:19] + "+00:00" if "+" not in ts and "Z" not in ts else ts.replace("Z", "+00:00")
        return None
    try:
        ts = int(ts)
        if ts > 1e12:
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat()
    except (ValueError, OSError):
        return None


# ---------------------------------------------------------------------------
# Transcript parsing — the rich source
# ---------------------------------------------------------------------------

def _project_transcript_dir(project_path: Path) -> Path:
    """Map project path to Claude's transcript directory."""
    normalized = str(project_path).replace("/", "-")
    return Path.home() / ".claude" / "projects" / normalized


def _extract_user_text(record: dict) -> str:
    """Extract user prompt text from a transcript record."""
    msg = record.get("message", record)
    content = msg.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b["text"])
        return " ".join(parts).strip()
    return ""


def _tool_summary(name: str, inp: dict) -> str:
    """Build a human-readable summary of a tool call."""
    if name in ("Edit", "Write", "MultiEdit", "ApplyPatch"):
        fp = inp.get("file_path") or inp.get("path") or ""
        short = fp.split("/")[-1] if "/" in fp else fp
        return f"{name.lower()}: {short}" if short else f"{name.lower()}"
    if name == "Bash":
        desc = inp.get("description", "")
        cmd = inp.get("command", "")
        cmd_short = cmd.strip().splitlines()[0][:80] if cmd else ""
        return desc or cmd_short or "bash command"
    if name in ("Read", "Glob"):
        target = inp.get("file_path") or inp.get("pattern") or ""
        return f"{name.lower()}: {target.split('/')[-1]}" if target else name.lower()
    if name == "Grep":
        pattern = inp.get("pattern", "")
        return f"grep: {pattern[:60]}" if pattern else "grep"
    if name == "Task":
        desc = inp.get("description", "")
        return f"task: {desc}" if desc else "task delegated"
    return f"{name.lower()}"


def _parse_transcript(path: Path) -> dict | None:
    """Parse a Claude transcript into a session summary.

    Returns dict with: timestamp, request, reasoning, files_changed, tools, duration_turns
    or None if the transcript is trivial.
    """
    try:
        records = [json.loads(line) for line in path.open("r", encoding="utf-8")]
    except Exception:
        return None

    user_texts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[str] = []
    files_changed: set[str] = set()
    first_ts: str | None = None
    last_ts: str | None = None

    for r in records:
        ts = _fmt_ts(r.get("timestamp"))
        if ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts

        rtype = r.get("type", r.get("role", ""))

        if rtype == "user":
            text = _extract_user_text(r)
            # Skip system/command messages
            if text and not text.startswith("<") and len(text) > 8:
                user_texts.append(text)

        elif rtype == "assistant":
            msg = r.get("message", {})
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    text = b["text"].strip()
                    if len(text) > 30:
                        reasoning_parts.append(text)
                elif b.get("type") == "tool_use":
                    name = b.get("name", "")
                    inp = b.get("input", {})
                    tool_calls.append(_tool_summary(name, inp))
                    if name in ("Edit", "Write", "MultiEdit", "ApplyPatch"):
                        fp = inp.get("file_path") or inp.get("path") or ""
                        if fp:
                            short = fp.split("/")[-1] if "/" in fp else fp
                            files_changed.add(short)

    # Skip trivial sessions (no real interaction)
    if not user_texts and not reasoning_parts:
        return None

    return {
        "timestamp": first_ts,
        "last_ts": last_ts,
        "session_id": path.stem,
        "user_requests": user_texts,
        "reasoning": reasoning_parts,
        "tool_calls": tool_calls,
        "files_changed": sorted(files_changed),
        "turns": len(user_texts),
    }


def _session_to_event(session: dict) -> Event:
    """Convert a parsed session summary into a rich Event."""
    # Build summary from first user request
    first_request = session["user_requests"][0] if session["user_requests"] else "agent session"
    summary = first_request.splitlines()[0][:200]

    # Build rich details with OTA structure
    parts: list[str] = []

    # What was requested (first 2 user prompts)
    for i, req in enumerate(session["user_requests"][:2]):
        label = "Request" if i == 0 else "Follow-up"
        parts.append(f"{label}: {req.splitlines()[0][:150]}")

    # What the agent reasoned (first 2 substantive reasoning blocks)
    for reasoning in session["reasoning"][:2]:
        first_line = reasoning.splitlines()[0][:150]
        parts.append(f"Reasoning: {first_line}")

    # What was done
    if session["files_changed"]:
        parts.append(f"Files changed: {', '.join(session['files_changed'][:8])}")
    if session["tool_calls"]:
        # Deduplicate and show unique tool actions
        unique_tools = list(dict.fromkeys(session["tool_calls"]))
        parts.append(f"Actions: {'; '.join(unique_tools[:5])}")

    details = "\n".join(parts)
    if len(details) > 1000:
        details = details[:1000].rsplit("\n", 1)[0]

    # OTA fields
    observation = summary
    thought = session["reasoning"][0].splitlines()[0][:200] if session["reasoning"] else ""
    action = "; ".join(session["tool_calls"][:3]) if session["tool_calls"] else ""

    return Event(
        agent="claude",
        channel="claude-transcript",
        tags=("session", "backfill"),
        summary=summary,
        details=details,
        timestamp=session["timestamp"],
        observation=observation,
        thought=thought,
        action=action,
    )


# ---------------------------------------------------------------------------
# Legacy history.jsonl parsing (Codex fallback)
# ---------------------------------------------------------------------------

def _legacy_to_event(record: dict, agent: str, channel: str) -> Event | None:
    text = record.get("display") or record.get("text") or ""
    if isinstance(text, list):
        text = " ".join(str(s) for s in text)
    text = text.strip()
    if not text or text in ("/clear", "/login", "/help", "/exit"):
        return None
    summary = text.splitlines()[0][:200]
    if len(summary.strip()) < 8:
        return None
    details = text if len(text) > 200 or "\n" in text else ""
    if len(details) > 800:
        details = details[:800].rsplit("\n", 1)[0] + "\n... [trimmed]"
    timestamp = _fmt_ts(record.get("timestamp") or record.get("ts"))
    return Event(
        agent=agent, channel=channel, tags=(agent, "backfill"),
        summary=summary, details=details, timestamp=timestamp,
        observation=summary,
    )


# ---------------------------------------------------------------------------
# Commit note builder
# ---------------------------------------------------------------------------

def _build_day_commit_notes(events: list[Event], day: str) -> str:
    """Build narrative commit notes from session-level events."""
    agent_counts: dict[str, int] = defaultdict(int)
    for ev in events:
        agent_counts[ev.agent] += 1
    agents_str = ", ".join(f"{a} ({c})" for a, c in sorted(agent_counts.items()))

    # For transcript-sourced events, show the rich details
    transcript_events = [e for e in events if e.channel == "claude-transcript"]
    legacy_events = [e for e in events if e.channel != "claude-transcript"]

    parts: list[str] = []
    parts.append(f"{len(events)} sessions from {agents_str}.")

    # Show transcript sessions (rich data)
    if transcript_events:
        parts.append("")
        for ev in transcript_events[:6]:
            ts_short = ev.timestamp[11:16] if ev.timestamp and len(ev.timestamp) > 16 else ""
            header = f"[{ts_short}] " if ts_short else ""
            parts.append(f"{header}{ev.summary[:120]}")
            # Show reasoning and files from details
            if ev.details:
                for line in ev.details.splitlines():
                    if line.startswith(("Reasoning:", "Files changed:")):
                        parts.append(f"  {line}")

    # Show legacy events (user prompts only)
    if legacy_events and not transcript_events:
        parts.append("")
        ranked = sorted(legacy_events, key=lambda e: len(e.summary), reverse=True)
        seen: set[str] = set()
        for ev in ranked:
            if len(seen) >= 5:
                break
            prefix = " ".join(ev.summary.split()[:3]).lower()
            if prefix in seen:
                continue
            seen.add(prefix)
            parts.append(f"- {ev.agent}: {ev.summary[:120]}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Claude/Codex histories into gcc-memory")
    parser.add_argument("project", help="Path to workspace whose .gcc should receive events")
    parser.add_argument("--branch", default="main", help="Branch to write events into")
    parser.add_argument(
        "--codex-history",
        default=os.path.expanduser("~/.codex/history.jsonl"),
        help="Path to Codex history JSONL",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be imported")
    args = parser.parse_args()

    project_path = Path(args.project).expanduser().resolve()
    store = ensure_project_store(project_path)
    ensure_global_store()

    existing_keys = build_existing_keys(store, args.branch)
    day_events: dict[str, list[Event]] = defaultdict(list)
    total = 0
    skipped = 0
    dropped = 0

    # --- Primary source: Claude session transcripts ---
    transcript_dir = _project_transcript_dir(project_path)
    session_count = 0
    if transcript_dir.is_dir():
        for tp in sorted(transcript_dir.glob("*.jsonl")):
            session = _parse_transcript(tp)
            if session is None:
                dropped += 1
                continue
            event = _session_to_event(session)
            key = (event.agent, event.channel, event.summary)
            if key in existing_keys:
                skipped += 1
                continue
            existing_keys.add(key)
            day = _date_key(event.timestamp)
            day_events[day].append(event)
            total += 1
            session_count += 1
    else:
        print(f"No transcript directory found at {transcript_dir}")

    # --- Fallback: Codex history.jsonl (user prompts only) ---
    codex_path = Path(args.codex_history)
    codex_count = 0
    for record in load_jsonl(codex_path):
        event = _legacy_to_event(record, agent="codex", channel="codex-history")
        if event is None:
            dropped += 1
            continue
        key = (event.agent, event.channel, event.summary)
        if key in existing_keys:
            skipped += 1
            continue
        existing_keys.add(key)
        day = _date_key(event.timestamp)
        day_events[day].append(event)
        total += 1
        codex_count += 1

    if args.dry_run:
        for day in sorted(day_events.keys()):
            events = day_events[day]
            transcripts = sum(1 for e in events if e.channel == "claude-transcript")
            legacy = len(events) - transcripts
            print(f"[DRY RUN] {day}: {len(events)} events ({transcripts} sessions, {legacy} prompts)")
            for ev in events[:3]:
                print(f"  {ev.agent}: {ev.summary[:100]}")
            if len(events) > 3:
                print(f"  ... and {len(events) - 3} more")
        print(f"\nWould import {total} events ({session_count} sessions, {codex_count} codex prompts) "
              f"across {len(day_events)} days, {skipped} duplicates, {dropped} dropped.")
        return

    # Import events and create commits grouped by day
    commits_created = 0
    for day in sorted(day_events.keys()):
        events = day_events[day]
        for event in events:
            store.append_event(args.branch, event)
        notes = _build_day_commit_notes(events, day)
        store.commit(args.branch, f"{day} ({len(events)} sessions)", notes)
        commits_created += 1

    # Populate per-branch metadata
    store.update_branch_metadata(args.branch)

    # Seed main.md Purpose with import stats
    first_day = min(day_events.keys()) if day_events else "unknown"
    last_day = max(day_events.keys()) if day_events else "unknown"
    store.update_main_section(
        "Purpose",
        f"Project memory seeded from transcripts ({first_day} to {last_day}). "
        f"{session_count} Claude sessions + {codex_count} Codex prompts across {commits_created} days. "
        f"Agents should curate this section with actual project goals.",
    )

    # Safety: ensure # Project Context h1 heading exists
    main_path = store.root / "main.md"
    if main_path.exists():
        content = main_path.read_text(encoding="utf-8")
        if not content.lstrip().startswith("# "):
            content = "# Project Context\n\n" + content.lstrip()
            main_path.write_text(content, encoding="utf-8")

    print(f"Imported {total} events ({session_count} Claude sessions, {codex_count} Codex prompts) "
          f"across {commits_created} daily commits, {skipped} duplicates, {dropped} dropped.")


if __name__ == "__main__":
    main()
