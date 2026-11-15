"""Cross-process file lock for wp-manager's JSON files.

Scheduled update alerts run in a separate process from the agent and the Desktop backend, so a
thread lock alone cannot stop two read/modify/write cycles from losing each other's changes.
POSIX uses ``fcntl.flock``; Windows uses ``msvcrt.locking``. Without either, it degrades to the
in-process lock only.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from pathlib import Path
from typing import Iterator

try:
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt  # type: ignore[import-not-found]
except ImportError:
    msvcrt = None  # type: ignore[assignment]

_thread_locks: dict[str, threading.RLock] = {}
_guard = threading.Lock()
_held = threading.local()


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _guard:
        return _thread_locks.setdefault(key, threading.RLock())


@contextlib.contextmanager
def locked(path: Path, timeout: float = 30.0) -> Iterator[None]:
    """Hold an exclusive lock on ``<path>.lock`` (re-entrant within a thread)."""
    lock_path = path.with_name(f".{path.name}.lock")
    tl = _thread_lock(lock_path)
    with tl:
        depth = getattr(_held, str(lock_path), 0)
        if depth:
            setattr(_held, str(lock_path), depth + 1)
            try:
                yield
            finally:
                setattr(_held, str(lock_path), depth)
            return
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _acquire(fd, timeout)
            setattr(_held, str(lock_path), 1)
            try:
                yield
            finally:
                setattr(_held, str(lock_path), 0)
                _release(fd)
        finally:
            os.close(fd)


def _acquire(fd: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise TimeoutError("timed out waiting for the wp-manager state lock")
            time.sleep(0.05)


def _release(fd: int) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        elif msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
    except OSError:
        pass
