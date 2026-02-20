import json

from gcc_memory import ContextStore
from gcc_memory import adapters


def test_parse_codex_json(tmp_path):
    payload = [
        {"agent": "codex", "channel": "shell", "text": "echo 1", "tags": ["cmd"]},
        {"agent": "codex", "channel": "shell", "text": "done"},
    ]
    path = tmp_path / "codex.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    events = adapters.parse_codex(path)
    assert len(events) == 2
    assert events[0].summary == "echo 1"


def test_ingest_into_store(tmp_path):
    store = ContextStore(tmp_path)
    store.init("demo", force=False)
    payload = {"turns": [{"speaker": "assistant", "content": "analysis here"}]}
    path = tmp_path / "claude.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    events = adapters.parse_claude(path)
    for event in events:
        store.append_event("main", event)

    assert store.recent_events("main", 1)[0]["agent"] == "assistant"
