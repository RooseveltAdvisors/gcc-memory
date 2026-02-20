from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

try:  # pragma: no cover - platform guard
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore

try:  # pragma: no cover - platform guard
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None  # type: ignore


def iso_now() -> str:
    """Return a UTC ISO 8601 timestamp."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Simple cross-platform file lock."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def atomic_write(path: Path, data: str) -> None:
    """Write text atomically to disk."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(data, encoding="utf-8")
    tmp_path.replace(path)
