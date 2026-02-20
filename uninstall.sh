#!/usr/bin/env bash
set -euo pipefail

echo "Uninstalling gcc-memory..."

# --- Remove hook files ---
rm -f "$HOME/.claude/hooks/gcc-memory_sync.py"
rm -f "$HOME/.claude/hooks/gcc-memory_stop.py"
rm -f "$HOME/.claude/hooks/gcc-memory_observe.py"
rm -f "$HOME/.claude/hooks/hook_common.py"
rm -f "$HOME/.codex/hooks/gcc-memory_notify.py"
echo "Removed hook files"

# --- Remove hook registrations from Claude settings.json ---
python3 - <<'PY'
import json
from pathlib import Path

settings_path = Path.home() / ".claude" / "settings.json"
if not settings_path.exists():
    print("No Claude settings.json found, skipping hook cleanup")
    raise SystemExit(0)

data = json.loads(settings_path.read_text())
hooks = data.get("hooks", {})

for event in ("PostToolUse", "Stop", "UserPromptSubmit"):
    entries = hooks.get(event, [])
    cleaned = []
    for block in entries:
        block_hooks = block.get("hooks", [])
        filtered = [h for h in block_hooks if "gcc-memory" not in (h.get("command") or "")]
        if filtered:
            new_block = dict(block)
            new_block["hooks"] = filtered
            cleaned.append(new_block)
    if cleaned:
        hooks[event] = cleaned
    elif event in hooks:
        del hooks[event]

settings_path.write_text(json.dumps(data, indent=2) + "\n")
print("Cleaned hook registrations from Claude settings.json")
PY

# --- Remove notify line from Codex config.toml ---
python3 - <<'PY'
from pathlib import Path

config_path = Path.home() / ".codex" / "config.toml"
if not config_path.exists():
    print("No Codex config.toml found, skipping")
    raise SystemExit(0)

lines = config_path.read_text().splitlines()
cleaned = [line for line in lines if "gcc-memory" not in line]
if len(cleaned) < len(lines):
    config_path.write_text("\n".join(cleaned) + "\n")
    print("Removed gcc-memory notify from Codex config.toml")
else:
    print("No gcc-memory entry in Codex config.toml")
PY

# --- Remove skill ---
if [ -d "$HOME/.claude/skills/gcc" ]; then
    rm -rf "$HOME/.claude/skills/gcc"
    echo "Removed gcc skill from ~/.claude/skills/"
fi

# --- Remove config ---
if [ -d "$HOME/.gcc-memory" ]; then
    rm -rf "$HOME/.gcc-memory"
    echo "Removed ~/.gcc-memory config"
fi

echo ""
echo "gcc-memory uninstalled."
echo ""
echo "Note: .gcc/ directories in your projects were NOT removed."
echo "To remove memory from a specific project:"
echo "  rm -rf /path/to/project/.gcc"
