#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install it from https://docs.astral.sh/uv/ and re-run ./install.sh" >&2
  exit 1
fi
cd "$ROOT"
uv sync
uv run scripts/setup_agent_hooks.sh
python3 - "$ROOT" <<'PY'
import json
import sys
from pathlib import Path
repo = Path(sys.argv[1]).resolve()
config_dir = Path.home() / ".gcc-memory"
config_dir.mkdir(parents=True, exist_ok=True)
config_path = config_dir / "config.json"
data = {"repo_path": str(repo)}
config_path.write_text(json.dumps(data, indent=2) + "\n")
print(f"Saved gcc-memory repo path to {config_path}")
PY
if [ -d "$ROOT/skills" ]; then
  python3 - "$ROOT/skills" <<'PY'
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

skills_src = Path(sys.argv[1])

# --- Sync to Claude ---
claude_dst = Path.home() / ".claude" / "skills"
claude_dst.mkdir(parents=True, exist_ok=True)
claude_copied = []
for entry in sorted(skills_src.iterdir(), key=lambda p: p.name):
    if not entry.is_dir() or entry.name.startswith("."):
        continue
    skill_md = entry / "SKILL.md"
    if not skill_md.exists():
        continue
    target = claude_dst / entry.name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(entry, target)
    claude_copied.append(entry.name)
if claude_copied:
    print("Synced Claude skills: " + ", ".join(claude_copied))

# --- Remove stale gcc-memory skills no longer in source ---
source_names = {e.name for e in skills_src.iterdir() if e.is_dir() and not e.name.startswith(".") and (e / "SKILL.md").exists()}
GCC_MEMORY_PREFIXES = ("gcc-memory-", "gcc")
removed = []
for installed in sorted(claude_dst.iterdir()):
    if not installed.is_dir():
        continue
    # Only clean up skills we own (gcc-memory-prefixed)
    if not any(installed.name.startswith(p) or installed.name == p for p in GCC_MEMORY_PREFIXES):
        continue
    if installed.name not in source_names:
        shutil.rmtree(installed)
        removed.append(installed.name)
if removed:
    print("Removed stale skills: " + ", ".join(removed))

# --- Rebuild Claude skill-index.json (merge, don't replace) ---
index_path = claude_dst / "skill-index.json"
if index_path.exists():
    index = json.loads(index_path.read_text())
else:
    index = {"generated": "", "totalSkills": 0, "alwaysLoadedCount": 0, "deferredCount": 0, "skills": {}}

for name in claude_copied:
    skill_md = claude_dst / name / "SKILL.md"
    text = skill_md.read_text()
    # Parse YAML frontmatter
    desc = ""
    skill_name = name
    if text.startswith("---"):
        end = text.index("---", 3)
        fm = text[3:end]
        for line in fm.strip().splitlines():
            if line.startswith("name:"):
                skill_name = line.split(":", 1)[1].strip()
            elif line.startswith("description:"):
                desc = line.split(":", 1)[1].strip()
    # Build trigger words from description
    stop_words = {"a", "an", "the", "or", "and", "for", "to", "in", "of", "is", "use", "when", "from", "into", "via", "with"}
    triggers = [w.lower().strip(".,") for w in desc.split() if w.lower().strip(".,") not in stop_words and len(w) > 2]
    tier = "deferred"
    key = name.lower().replace("-", "")
    if text.startswith("---"):
        end = text.index("---", 3)
        fm = text[3:end]
        for line in fm.strip().splitlines():
            if line.startswith("tier:"):
                tier = line.split(":", 1)[1].strip()
    index["skills"][key] = {
        "name": skill_name,
        "path": f"{name}/SKILL.md",
        "fullDescription": desc,
        "triggers": triggers,
        "workflows": [],
        "tier": tier,
    }

# Remove stale entries from index
for r in removed:
    key = r.lower().replace("-", "")
    index["skills"].pop(key, None)

index["generated"] = datetime.now(timezone.utc).isoformat()
index["totalSkills"] = len(index["skills"])
index["alwaysLoadedCount"] = sum(1 for s in index["skills"].values() if s.get("tier") == "always")
index["deferredCount"] = sum(1 for s in index["skills"].values() if s.get("tier") == "deferred")
index_path.write_text(json.dumps(index, indent=2) + "\n")
print(f"Updated Claude skill-index.json ({len(claude_copied)} skills added/updated)")
PY
fi
echo "Done. Restart Claude Code and Codex to load the new hooks and skills."
