from __future__ import annotations

import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .store import ContextStore, Event


class _ContextHandler(BaseHTTPRequestHandler):
    store: ContextStore
    default_branch: Optional[str]

    def _json_response(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:  # pragma: no cover - should be rare
            raise ValueError(f"Invalid JSON: {exc}") from exc

    def do_GET(self) -> None:  # noqa: N802 - framework API
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json_response({"status": "ok"})
            return
        if parsed.path == "/branches":
            self._json_response({"branches": self.store.list_branches(), "active": self.store.get_active_branch()})
            return
        if parsed.path == "/context":
            params = parse_qs(parsed.query)
            mode = params.get("mode", ["status"])[0]
            branch = params.get("branch", [self.default_branch or self.store.get_active_branch()])[0]
            limit = int(params.get("limit", ["10"])[0])
            offset = int(params.get("offset", ["0"])[0])

            if mode == "branch":
                self._json_response(self.store.context_branch(branch, offset=offset, limit=limit))
            elif mode == "commit":
                index = int(params.get("index", ["0"])[0])
                self._json_response(self.store.context_commit(branch, index))
            elif mode == "log":
                self._json_response(self.store.context_log(branch, offset=offset, limit=limit))
            elif mode == "metadata":
                segment = params.get("segment", [None])[0]
                self._json_response(self.store.context_metadata(segment))
            elif mode == "snapshot":
                self._json_response(self.store.snapshot(branch, limit=limit))
            else:
                # Default: status overview
                self._json_response(self.store.context_status())
            return
        if parsed.path == "/stream":
            params = parse_qs(parsed.query)
            branch = params.get("branch", [self.default_branch or self.store.get_active_branch()])[0]
            self._stream(branch)
            return
        self._json_response({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - framework API
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/log":
                payload = self._read_json()
                branch = payload.get("branch") or self.store.get_active_branch()
                body = payload.get("body") or payload.get("details") or ""
                summary = payload.get("summary") or (body.splitlines()[0] if body else "")
                event = Event(
                    agent=payload.get("agent") or payload.get("source") or "agent",
                    channel=payload.get("channel", "shell"),
                    tags=tuple(payload.get("tags", [])),
                    summary=summary,
                    details=body,
                    payload=payload.get("payload"),
                )
                self.store.append_event(branch, event)
                self._json_response({"status": "logged", "branch": branch})
                return
            if parsed.path == "/commit":
                payload = self._read_json()
                branch = payload.get("branch") or self.store.get_active_branch()
                title = payload.get("title", "API commit")
                notes = payload.get("notes")
                stamp = self.store.commit(branch, title, notes)
                self._json_response({"status": "committed", "branch": branch, "timestamp": stamp})
                return
            if parsed.path == "/merge":
                payload = self._read_json()
                source = payload["source"]
                target = payload.get("target") or self.store.get_active_branch()
                self.store.merge(source, target)
                self._json_response({"status": "merged", "source": source, "target": target})
                return
        except Exception as exc:  # pragma: no cover - simple server
            self._json_response({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._json_response({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def _stream(self, branch: str) -> None:
        try:
            events = list(self.store.iter_events(branch))
        except Exception as exc:
            self._json_response({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        seen = len(events)
        for event in events[-10:]:
            self._emit_event(event)
        try:
            while True:
                docs = list(self.store.iter_events(branch)) or []
                if len(docs) > seen:
                    for event in docs[seen:]:
                        self._emit_event(event)
                    seen = len(docs)
                time.sleep(1)
        except BrokenPipeError:
            return

    def _emit_event(self, event: Dict[str, Any]) -> None:
        payload = json.dumps(event)
        message = f"data: {payload}\n\n".encode("utf-8")
        self.wfile.write(message)
        self.wfile.flush()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003 - API name
        return  # silence default logging


def serve_http(store: ContextStore, *, host: str, port: int, branch: Optional[str] = None) -> None:
    def handler(*args: Any, **kwargs: Any) -> None:
        _ContextHandler.store = store
        _ContextHandler.default_branch = branch
        _ContextHandler(*args, **kwargs)

    server = ThreadingHTTPServer((host, port), handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - CLI use only
        server.shutdown()
