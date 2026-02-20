from pathlib import Path

from gcc_memory.agent_runtime import ensure_project_store, ensure_global_store, search
from gcc_memory.store import Event


def test_ensure_and_search(tmp_path, monkeypatch):
    monkeypatch.setattr("gcc_memory.agent_runtime.GLOBAL_ROOT", tmp_path / "global")
    project = tmp_path / "proj"
    store = ensure_project_store(project, description="proj")
    store.append_event("main", Event(agent="codex", channel="shell", summary="Check", details="Details"))
    global_store = ensure_global_store()
    global_store.append_event("main", Event(agent="claude", channel="chat", summary="Global info", details=""))
    matches = search(global_store, "Global")
    assert matches and matches[0]["agent"] == "claude"
