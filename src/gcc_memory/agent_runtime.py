from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .store import ContextStore, Event

GCC_MEMORY_HOME = Path.home() / ".gcc-memory"
GLOBAL_ROOT = GCC_MEMORY_HOME / "global"


def ensure_project_store(path: Path, description: Optional[str] = None) -> ContextStore:
    store = ContextStore(path)
    if not store.root.exists():
        store.init(description=description or path.name)
    return store


def ensure_global_store(description: str = "Global context") -> ContextStore:
    store = ContextStore(GLOBAL_ROOT)
    if not store.root.exists():
        GLOBAL_ROOT.mkdir(parents=True, exist_ok=True)
        store.init(description=description)
    return store


def log_global_event(summary: str, details: str, agent: str = "agent", branch: str = "main") -> None:
    store = ensure_global_store()
    if branch not in store.list_branches():
        store.create_branch(branch, parent="main", summary=f"Global branch: {branch}")
    store.append_event(branch, Event(agent=agent, channel="global", summary=summary, details=details))


def search(store: ContextStore, query: str, limit: int = 5) -> List[Dict[str, str]]:
    matches: List[Dict[str, str]] = []
    query_lower = query.lower()
    for branch in store.list_branches():
        for event in store.iter_events(branch):
            haystack = f"{event.get('summary', '')}\n{event.get('body', '')}".lower()
            if query_lower in haystack:
                matches.append(
                    {
                        "branch": branch,
                        "timestamp": event.get("timestamp", ""),
                        "agent": event.get("agent", ""),
                        "summary": event.get("summary", ""),
                    }
                )
                if len(matches) >= limit:
                    return matches
    return matches
