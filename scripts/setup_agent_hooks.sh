#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE_HOOK="$HOME/.claude/hooks/gcc-memory_sync.py"
CLAUDE_HOOK_COMMON="$HOME/.claude/hooks/hook_common.py"
CLAUDE_STOP_HOOK="$HOME/.claude/hooks/gcc-memory_stop.py"
CLAUDE_OBSERVE_HOOK="$HOME/.claude/hooks/gcc-memory_observe.py"
CODEX_HOOK="$HOME/.codex/hooks/gcc-memory_notify.py"
PYTHON_BIN="$ROOT/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    echo "python3 not found. Install Python or run uv sync to create .venv." >&2
    exit 1
  fi
fi
mkdir -p "$(dirname "$CLAUDE_HOOK")"
install -m 755 "$ROOT/integrations/claude/hook_common.py" "$CLAUDE_HOOK_COMMON"
install -m 755 "$ROOT/integrations/claude/gcc_memory_sync.py" "$CLAUDE_HOOK"
install -m 755 "$ROOT/integrations/claude/gcc_memory_stop.py" "$CLAUDE_STOP_HOOK"
install -m 755 "$ROOT/integrations/claude/gcc_memory_observe.py" "$CLAUDE_OBSERVE_HOOK"
if [ -f "$ROOT/integrations/codex/gcc-memory_notify.py" ]; then
  mkdir -p "$(dirname "$CODEX_HOOK")"
  install -m 755 "$ROOT/integrations/codex/gcc-memory_notify.py" "$CODEX_HOOK"
fi
CLAUDE_CMD="$PYTHON_BIN $CLAUDE_HOOK"
CLAUDE_STOP_CMD="$PYTHON_BIN $CLAUDE_STOP_HOOK"
CLAUDE_OBSERVE_CMD="$PYTHON_BIN $CLAUDE_OBSERVE_HOOK"
CODEX_BIN="$PYTHON_BIN"
CLAUDE_CMD_ENV="$CLAUDE_CMD" CLAUDE_STOP_CMD_ENV="$CLAUDE_STOP_CMD" CLAUDE_OBSERVE_CMD_ENV="$CLAUDE_OBSERVE_CMD" python3 - <<'PY'
import json
import os
from pathlib import Path

settings_path = Path.home() / ".claude" / "settings.json"
if not settings_path.exists():
    raise SystemExit("CLAUDE settings.json not found")

data = json.loads(settings_path.read_text())
hooks = data.setdefault("hooks", {})

def remove_aline(event: str) -> None:
    entries = hooks.get(event, [])
    cleaned = []
    for block in entries:
        existing = block.get("hooks", [])
        filtered = [hook for hook in existing if "aline-ai" not in (hook.get("command") or "")]
        if filtered:
            new_block = dict(block)
            new_block["hooks"] = filtered
            cleaned.append(new_block)
    if cleaned:
        hooks[event] = cleaned
    elif event in hooks:
        hooks.pop(event)

remove_aline("Stop")
remove_aline("UserPromptSubmit")

# --- PostToolUse (gcc-memory_sync) ---
post_tool = hooks.setdefault("PostToolUse", [])
sync_target = str(Path.home() / '.claude' / 'hooks' / 'gcc-memory_sync.py')
filtered = []
for block in post_tool:
    existing = block.get("hooks", [])
    if any(sync_target in (hook.get("command") or "") for hook in existing):
        continue
    filtered.append(block)
post_tool[:] = filtered
command = os.environ["CLAUDE_CMD_ENV"]
entry = {
    "matcher": "Bash|Write|Edit|ApplyPatch|Plan|Task|MultiEdit",
    "hooks": [
        {
            "type": "command",
            "command": command,
            "timeout": 30
        }
    ]
}
if not any(block.get("hooks") and block["hooks"][0].get("command") == command for block in post_tool):
    post_tool.append(entry)

# --- Helper to register a hook on an event, appending without clobbering ---
def register_hook(event: str, cmd: str, matcher: str = "") -> None:
    event_list = hooks.setdefault(event, [])
    # Remove any existing gcc-memory entry for this event
    hook_file = cmd.split()[-1] if cmd else ""
    cleaned = []
    for block in event_list:
        existing = block.get("hooks", [])
        if any(hook_file in (h.get("command") or "") for h in existing):
            continue
        cleaned.append(block)
    event_list[:] = cleaned
    new_entry: dict = {
        "hooks": [
            {
                "type": "command",
                "command": cmd,
                "timeout": 30
            }
        ]
    }
    if matcher:
        new_entry["matcher"] = matcher
    if not any(block.get("hooks") and block["hooks"][0].get("command") == cmd for block in event_list):
        event_list.append(new_entry)

# --- Stop hook (gcc-memory_stop) ---
register_hook("Stop", os.environ["CLAUDE_STOP_CMD_ENV"])

# --- UserPromptSubmit hook (gcc-memory_observe) ---
register_hook("UserPromptSubmit", os.environ["CLAUDE_OBSERVE_CMD_ENV"])

settings_path.write_text(json.dumps(data, indent=2) + "\n")
PY

if [ -f "$HOME/.codex/config.toml" ]; then
  CODEX_BIN_ENV="$CODEX_BIN" CODEX_HOOK_ENV="$CODEX_HOOK" python3 - <<'PY'
from pathlib import Path
import os
import json

config_path = Path.home() / ".codex" / "config.toml"
hook_line = "notify = " + json.dumps([os.environ["CODEX_BIN_ENV"], Path(os.environ["CODEX_HOOK_ENV"]).as_posix()])
lines = config_path.read_text().splitlines()
replaced = False
for idx, line in enumerate(lines):
    if line.strip().startswith("notify"):
        lines[idx] = hook_line
        replaced = True
        break
if not replaced:
    insert_idx = 0
    for idx, line in enumerate(lines):
        if line.strip().startswith("["):
            insert_idx = idx
            break
        insert_idx = idx + 1
    lines.insert(insert_idx, hook_line)
config_path.write_text("\n".join(lines) + "\n")
print("Configured Codex hooks")
PY
else
  echo "Codex not found, skipping Codex hook setup"
fi

echo "Installed gcc-memory hooks"
