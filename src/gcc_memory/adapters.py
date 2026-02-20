from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, List, Sequence

from .store import Event

_TEXT_KEYS = ("body", "text", "message", "content", "details")
_AGENT_KEYS = ("agent", "speaker", "role", "who")
_CHANNEL_KEYS = ("channel", "mode", "source")
_TAG_KEYS = ("tags", "labels")
_TIMESTAMP_KEYS = ("timestamp", "time", "ts")


@dataclass(slots=True)
class AdapterSpec:
    name: str
    default_agent: str
    default_channel: str
    default_tags: Sequence[str]


def _load_records(path: Path) -> List[dict]:
    text = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        records = []
        for line in text.splitlines():
            striped = line.strip()
            if not striped:
                continue
            records.append(json.loads(striped))
        if records:
            return records
        raise
    if isinstance(parsed, dict):
        for key in ("turns", "messages", "events"):
            if key in parsed and isinstance(parsed[key], list):
                return parsed[key]
        return [parsed]
    if isinstance(parsed, list):
        return parsed
    raise ValueError("Unsupported transcript payload")


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(_coerce_text(item) for item in value)
    if isinstance(value, dict):
        return _coerce_text(value.get("text") or value.get("value") or value.get("body"))
    return str(value)


def _extract_first(record: dict, keys: Sequence[str], default: str) -> str:
    for key in keys:
        if key in record and record[key]:
            return _coerce_text(record[key])
    return default


def _extract_tags(record: dict, defaults: Sequence[str]) -> Sequence[str]:
    for key in _TAG_KEYS:
        value = record.get(key)
        if isinstance(value, list):
            return [str(v) for v in value if v]
        if isinstance(value, str):
            return [value]
    return defaults


def _extract_payload(record: dict) -> dict:
    ignore = set(_TEXT_KEYS + _AGENT_KEYS + _CHANNEL_KEYS + _TAG_KEYS + _TIMESTAMP_KEYS + ("summary",))
    payload = {k: v for k, v in record.items() if k not in ignore}
    return payload


def parse_transcript(path: Path, spec: AdapterSpec) -> List[Event]:
    try:
        chunks = _load_records(path)
    except Exception:
        body = path.read_text(encoding="utf-8")
        event = Event(
            agent=spec.default_agent,
            channel=spec.default_channel,
            tags=tuple(spec.default_tags),
            summary=body.splitlines()[0] if body else "",
            details=body,
        )
        return [event]

    events: List[Event] = []
    for record in chunks:
        if not isinstance(record, dict):
            continue
        body = _extract_first(record, _TEXT_KEYS, "")
        summary = record.get("summary") or (body.splitlines()[0] if body else "")
        agent = _extract_first(record, _AGENT_KEYS, spec.default_agent)
        channel = _extract_first(record, _CHANNEL_KEYS, spec.default_channel)
        tags = tuple(_extract_tags(record, spec.default_tags))
        timestamp = record.get("timestamp") or record.get("ts") or record.get("time")
        payload = _extract_payload(record)
        event = Event(
            agent=agent,
            channel=channel,
            tags=tags,
            summary=summary,
            details=body,
            payload=payload or None,
            timestamp=timestamp or None,  # type: ignore[arg-type]
        )
        events.append(event)
    return events


CODEX_SPEC = AdapterSpec("codex", default_agent="codex", default_channel="shell", default_tags=("codex",))
CLAUDE_SPEC = AdapterSpec("claude", default_agent="claude", default_channel="chat", default_tags=("claude",))
OPEN_CODE_SPEC = AdapterSpec("opencode", default_agent="opencode", default_channel="shell", default_tags=("opencode",))


def parse_codex(path: Path) -> List[Event]:
    return parse_transcript(path, CODEX_SPEC)


def parse_claude(path: Path) -> List[Event]:
    return parse_transcript(path, CLAUDE_SPEC)


def parse_opencode(path: Path) -> List[Event]:
    return parse_transcript(path, OPEN_CODE_SPEC)
