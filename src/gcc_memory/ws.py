from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from websockets.asyncio.server import ServerProtocol as AsyncServerProtocol
from websockets.asyncio.server import serve as ws_serve

from .store import ContextStore


class WebSocketRelay:
    def __init__(self, store: ContextStore, branch: Optional[str] = None, poll_interval: float = 1.0, replay: int = 10):
        self.store = store
        self.default_branch = branch
        self.poll_interval = poll_interval
        self.replay = replay

    async def handler(self, websocket: AsyncServerProtocol) -> None:
        branch = (websocket.path or "/").lstrip("/") or self.store.get_active_branch()
        await self._stream_branch(websocket, branch)

    async def _stream_branch(self, websocket: AsyncServerProtocol, branch: str) -> None:
        seen = 0
        event_buffer = list(self.store.iter_events(branch))
        if self.replay:
            for event in event_buffer[-self.replay :]:
                await self._send(websocket, event)
        seen = len(event_buffer)
        try:
            while True:
                events = list(self.store.iter_events(branch))
                if len(events) > seen:
                    for event in events[seen:]:
                        await self._send(websocket, event)
                    seen = len(events)
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:
            return

    async def _send(self, websocket: AsyncServerProtocol, event: Dict[str, Any]) -> None:
        await websocket.send(json.dumps(event))

    async def run(self, host: str, port: int) -> None:
        async with ws_serve(self.handler, host, port):
            await asyncio.Future()


def serve_ws(store: ContextStore, *, host: str, port: int, branch: Optional[str] = None) -> None:
    relay = WebSocketRelay(store, branch=branch)
    asyncio.run(relay.run(host, port))
