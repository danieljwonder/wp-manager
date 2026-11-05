"""Where wp-manager keeps its files: ``<HERMES_HOME>/wp-manager/``.

The Hermes home is resolved at call time through Hermes's own helper so the active profile is
honoured (the dashboard scopes ``?profile=`` requests by home). Tests install an override.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_override: Optional[Path] = None


class HomeUnavailable(RuntimeError):
    pass


def set_home_override(path: Optional[os.PathLike]) -> None:
    """Pin the Hermes home (tests). ``None`` restores normal resolution."""
    global _override
    _override = Path(path) if path is not None else None


def hermes_home() -> Path:
    if _override is not None:
        return _override
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except ImportError:
        pass
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return Path(env)
    raise HomeUnavailable("cannot resolve the Hermes home directory (run inside Hermes or set HERMES_HOME)")


def data_dir() -> Path:
    d = hermes_home() / "wp-manager"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def sites_file() -> Path:
    return data_dir() / "sites.json"


def state_file() -> Path:
    return data_dir() / "state.json"


def actions_file() -> Path:
    return data_dir() / "actions.jsonl"


def env_file() -> Path:
    return hermes_home() / ".env"


def write_private(path: Path, text: str) -> None:
    """Atomically replace ``path`` with ``text``, mode 0600."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
