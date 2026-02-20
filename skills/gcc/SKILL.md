---
name: gcc
description: Agent-driven memory via GCC commands. COMMIT progress, BRANCH explorations, MERGE results, CONTEXT to orient.
tier: always
triggers:
  - commit
  - checkpoint
  - branch
  - merge
  - context
  - memory
  - progress
  - roadmap
  - gcc
  - backfill
  - history
  - import
---

# GCC — Agent-Driven Memory

You have a **Git-inspired Contextual Commit (GCC)** memory system. Use it proactively during work — commit milestones, branch before experiments, orient with context. **You are responsible for the quality of what gets recorded.** Auto-commit is a fallback; your narrative commits and curated summaries are what make this memory useful to future sessions.

## CLI Setup

```bash
GCC_MEMORY_REPO=$(python3 -c "import json; print(json.load(open('$HOME/.gcc-memory/config.json'))['repo_path'])")
alias gcc-memory="$GCC_MEMORY_REPO/.venv/bin/gcc-memory"
```

Run this once per session. All commands below assume the alias is set.

## Commands

### COMMIT — after completing a coherent milestone

```bash
gcc-memory commit --title "Implement JWT auth" \
  --notes "Replaced session cookies with JWT tokens. Simplifies API gateway and enables stateless scaling. Validated with integration tests."
```

Add `--git` to also create a Git commit, checkpointing both code and memory state:
```bash
gcc-memory commit --title "Implement JWT auth" --notes "..." --git
```

Notes must explain **what changed and why** — not list files or echo commands. Write as if briefing an engineer joining tomorrow. Commit after features, fixes, refactors, successful tests, or before task switches.

Bad: `--notes "edited io.py, ran tests"` (too terse, no reasoning)
Good: `--notes "Defines a reusable file output abstraction write_file(path, content) in io.py. Validated with a test to ensure correctness and future extensibility."`

### BRANCH — before exploring an uncertain alternative

```bash
gcc-memory branch create try-async-api --summary "Testing async endpoints for performance" --activate
```

### MERGE — when a branch experiment succeeds

```bash
gcc-memory merge try-async-api --target main
```

Add `--git` to also create a Git commit for the merge:
```bash
gcc-memory merge try-async-api --target main --git
```

The merge command automatically retrieves the target branch's context first (showing its progress and history), then integrates the source branch's events and creates a merge commit entry.

After merge: curate main.md — update Active Decisions with what was decided, remove resolved Pending Questions.

### CONTEXT — to orient yourself

```bash
gcc-memory context                          # project overview + branches
gcc-memory context --branch main            # branch purpose + commits
gcc-memory context --commit 0               # latest commit detail
gcc-memory context --log --limit 20         # execution trace
gcc-memory context --metadata file_structure # project files
```

Use at session start, before merges, or when disoriented.

### UPDATE-MAIN — curate the project roadmap

```bash
gcc-memory update-main --section "Purpose" \
  --content "Full-stack Next.js portal for healthcare analytics. Core features: SEO analysis, marketing sync, patient engagement dashboards."
```

```bash
gcc-memory update-main --section "Active Decisions" \
  --content "- JWT for auth (decided in try-jwt branch)
- PostgreSQL for persistence
- Tailwind CSS for styling"
```

Sections: **Purpose**, **Milestones**, **To-Do**, **Active Decisions**, **Pending Questions**. These are the most-read parts of memory — keep them current, specific, and useful.

## Curation — Your Core Responsibility

The value of GCC depends on you writing meaningful content, not on automation. Specifically:

**At session start:** Run `gcc-memory context`. If main.md sections contain placeholders like "(none yet)" or "(pending curation)", curate them based on what you learn during the session.

**After backfill:** The backfill script imports raw history but cannot summarize it intelligently. After a backfill completes:
1. Run `gcc-memory context --log --limit 50` to understand what happened
2. Run `gcc-memory update-main --section "Purpose" --content "..."` with a real project description
3. Update Active Decisions and Pending Questions based on patterns you see in the history

**After major milestones:** Commit first, then review whether main.md sections need updating. Update Milestones with what was achieved. Move completed To-Do items. Active Decisions should reflect current architectural choices. Pending Questions should list real open issues.

**After merges:** Update Active Decisions with what was decided. Remove resolved Pending Questions. Check off completed To-Do items and add any new ones.

## Workflow

1. **Start session** → `gcc-memory context` to orient
2. **Before experiment** → `gcc-memory branch create ...`
3. **After milestone** → `gcc-memory commit --title ... --notes ...`
4. **Experiment worked** → `gcc-memory merge ...`
5. **Curate** → `gcc-memory update-main ...` when sections are stale

Auto-commit fires every 5 min as fallback, but your narrative commits are far more valuable.

## BACKFILL — import past history

```bash
$GCC_MEMORY_REPO/scripts/run_backfill.sh /path/to/workspace --dry-run   # preview
$GCC_MEMORY_REPO/scripts/run_backfill.sh /path/to/workspace --branch main  # import
```

Imports `~/.claude/history.jsonl` and `~/.codex/history.jsonl`. Creates daily commits grouped by day. Deduplicates — safe to run repeatedly. **After backfill, curate main.md** — the script seeds placeholders that you should replace with real content.
