"""Shared utilities for gcc-memory Claude hooks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

MAX_FIELD = 8000
DEBOUNCE_SECONDS = 3
CONFIG_PATH = Path.home() / ".gcc-memory" / "config.json"


def coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2)
    except TypeError:
        return str(value)


def trim(text: str) -> str:
    text = text.strip()
    if len(text) > MAX_FIELD:
        return text[:MAX_FIELD] + "\n... [truncated]"
    return text


def resolve_repo_root() -> Path:
    env = os.environ.get("GCC_MEMORY_REPO")
    if env:
        return Path(env).expanduser()
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            repo_path = data.get("repo_path")
            if repo_path:
                return Path(repo_path).expanduser()
        except Exception:
            pass
    return Path.home() / "Git" / "gcc-memory"


def script_path(relative: str) -> Path:
    return resolve_repo_root() / relative


def run_script(script: Path, args: list[str]) -> None:
    if not script.exists():
        return
    repo = resolve_repo_root()
    env = {**os.environ, "PYTHONPATH": f"{repo / 'src'}:{os.environ.get('PYTHONPATH', '')}"}
    try:
        subprocess.run(
            [sys.executable, str(script), *args],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except Exception as exc:
        print(f"hook-warning: unable to run {script.name} ({exc})", file=sys.stderr)


def load_gcc_memory():
    repo = resolve_repo_root()
    src = repo / "src"
    if src.exists():
        sys.path.insert(0, str(src))
    try:
        from gcc_memory.agent_runtime import ensure_project_store, ensure_global_store
        from gcc_memory.store import Event
    except Exception as exc:
        print(f"hook-error: unable to import gcc_memory ({exc})", file=sys.stderr)
        return None, None, None
    return ensure_project_store, ensure_global_store, Event


def ensure_branch(cwd: Path) -> None:
    branch = os.environ.get("GCC_MEMORY_BRANCH")
    if not branch:
        return
    script = script_path("scripts/hooks/ensure_branch.py")
    run_script(script, [str(cwd), "--branch", branch])


def is_debounced(store, branch: str, agent_name: str, key: str) -> bool:
    """Check if an identical agent+key event was logged within DEBOUNCE_SECONDS."""
    debounce_dir = store.root / ".debounce"
    debounce_dir.mkdir(parents=True, exist_ok=True)
    marker_key = f"{branch}.{agent_name}.{key}"
    marker = debounce_dir / marker_key
    now = time.time()
    if marker.exists():
        try:
            last = marker.stat().st_mtime
            if now - last < DEBOUNCE_SECONDS:
                return True
        except OSError:
            pass
    try:
        marker.write_text(str(int(now)), encoding="utf-8")
    except OSError:
        pass
    return False
