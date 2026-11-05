"""Credential resolution. Sites store a *reference*; the Application Password is resolved per call.

* ``{"env": "WPM_<ID>_APP_PASSWORD"}`` (default) — ``agent.secret_scope.get_secret``: covers the
  profile's ``.env`` and every Hermes secret source (1Password, Bitwarden, command helper).
* ``{"op": "op://Vault/Item/field"}`` — ``op read`` run directly (argument list, no shell, 15 s).

Resolved values are registered with :mod:`redact` so they are masked in every output.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

from . import paths, redact, registry

OP_TIMEOUT = 15
CONNECTION_PREFIX = "wpmh1:"
MAX_PASSWORD_LEN = 200


class CredentialError(Exception):
    """Credential could not be resolved / decoded. Messages never contain the secret."""


# ------------------------------------------------------------------ swappable sources (tests)
def _default_secret_getter(name: str) -> Optional[str]:
    try:
        from agent.secret_scope import get_secret
    except ImportError:
        return os.environ.get(name)
    return get_secret(name)


def _default_op_runner(binary: str, ref: str) -> str:
    exe = shutil.which(binary) if os.sep not in binary else (binary if os.path.isfile(binary) else None)
    if not exe:
        raise CredentialError(
            f"1Password CLI {binary!r} not found; install `op` or set op_binary, or use a Hermes secret "
            "(hermes secrets onepassword set ...) instead"
        )
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [exe, "read", "--no-newline", ref],
            capture_output=True,
            text=True,
            timeout=OP_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CredentialError(f"`op read` timed out after {OP_TIMEOUT}s (is 1Password unlocked?)") from exc
    except OSError as exc:
        raise CredentialError(f"could not run `op`: {type(exc).__name__}") from exc
    if proc.returncode != 0:
        lines = (proc.stderr or "").strip().splitlines()
        detail = redact.scrub(lines[-1])[:200] if lines else f"exit {proc.returncode}"
        raise CredentialError(f"`op read` failed: {detail}")
    return proc.stdout


SecretGetter = Callable[[str], Optional[str]]
OpRunner = Callable[[str, str], str]
_secret_getter: SecretGetter = _default_secret_getter
_op_runner: OpRunner = _default_op_runner
# Values this process wrote with the built-in .env writer (Hermes's own writer publishes to the
# live secret scope itself). Lets the site work right away, before Hermes reloads its .env.
_written: dict[str, str] = {}


def set_secret_getter(fn: Optional[SecretGetter]) -> None:
    global _secret_getter
    _secret_getter = fn or _default_secret_getter


def set_op_runner(fn: Optional[OpRunner]) -> None:
    global _op_runner
    _op_runner = fn or _default_op_runner


# ------------------------------------------------------------------ resolution
def _clean_password(value: Any) -> str:
    if not isinstance(value, str):
        raise CredentialError("credential is empty")
    v = value.strip()
    if not v or len(v) > MAX_PASSWORD_LEN or any(c in v for c in "\r\n\x00"):
        raise CredentialError("credential is empty or malformed")
    return v


def resolve(site: dict, op_binary: str = "op") -> str:
    """Return the Application Password for ``site``. Raises CredentialError (no secret in message)."""
    ref = site.get("credential") or {"env": registry.default_env_name(site["id"])}
    try:
        ref = registry.check_credential_ref(ref)
    except registry.RegistryError as exc:
        raise CredentialError(str(exc)) from exc
    if "env" in ref:
        name = ref["env"]
        try:
            value = _secret_getter(name) or _written.get(name)
        except Exception as exc:  # UnscopedSecretError and friends
            raise CredentialError(f"secret {name} could not be read: {type(exc).__name__}") from exc
        if not value:
            raise CredentialError(
                f"secret {name} is not set. Store it with e.g. `hermes secrets onepassword set {name} "
                f"op://Vault/Item/password`, or add {name}=... to the Hermes .env"
            )
    else:
        if not re.match(r"^[A-Za-z0-9_./\\:-]{1,255}$", op_binary or "") or op_binary.startswith("-"):
            raise CredentialError("op_binary setting is not a valid program name or path")
        value = _op_runner(op_binary, ref["op"])
    password = _clean_password(value)
    redact.remember(password, site.get("username"))
    return password


def where(ref: dict) -> str:
    return registry.credential_label(ref)


# ------------------------------------------------------------------ connection strings
def decode_connection_string(raw: Any) -> dict:
    """Decode ``wpmh1:<base64 JSON {v, url, user, app_password}>``. Never echoes the input."""
    if not isinstance(raw, str):
        raise CredentialError("connection string must be text")
    s = "".join(raw.split())
    if not s.startswith(CONNECTION_PREFIX):
        raise CredentialError("not a wp-manager connection string (it should start with wpmh1:)")
    payload = s[len(CONNECTION_PREFIX):]
    if not payload or len(payload) > 4096:
        raise CredentialError("connection string is empty or too long")
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.b64decode(payload.replace("-", "+").replace("_", "/"), validate=True)
        data = json.loads(decoded.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise CredentialError("connection string is damaged (copy it again from Tools → Hermes Connector)") from exc
    if not isinstance(data, dict) or str(data.get("v")) != "1":
        raise CredentialError("unsupported connection string version")
    url, user, pw = data.get("url"), data.get("user"), data.get("app_password")
    if not all(isinstance(x, str) and x.strip() for x in (url, user, pw)):
        raise CredentialError("connection string is missing url, user or app_password")
    password = _clean_password(pw)
    redact.remember(password, user.strip())
    return {"url": url.strip(), "user": user.strip(), "app_password": password}


# ------------------------------------------------------------------ .env fallback writer
def _hermes_env_writer() -> Optional[Callable[[str, str], None]]:
    try:
        from hermes_cli import config as hconfig
    except ImportError:
        return None
    save = getattr(hconfig, "save_env_value", None)
    check = getattr(hconfig, "require_env_writable", None)
    target = getattr(hconfig, "get_env_path", None)
    if save is None or target is None:
        return None
    try:
        # Only delegate when Hermes would write the same file wp-manager resolved (the active
        # profile's .env); otherwise (a pinned home in tests or tooling) use the built-in writer.
        if Path(target()).resolve() != paths.env_file().resolve():
            return None
    except Exception:
        return None

    def write(name: str, value: str) -> None:
        if check is not None:
            check(name, "set")  # raises ValueError when the .env write lock refuses this key
        save(name, value)

    return write


_env_writer_override: Optional[Callable[[str, str], None]] = None


def set_env_writer(fn: Optional[Callable[[str, str], None]]) -> None:
    """Tests: replace Hermes's writer (``None`` → use Hermes's when importable, else built-in)."""
    global _env_writer_override
    _env_writer_override = fn


def _builtin_env_write(name: str, value: str) -> None:
    path = paths.env_file()
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    pat = re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=")
    entry = f"{name}={value}\n"
    replaced = False
    out = []
    for line in lines:
        if pat.match(line):
            if not replaced:
                out.append(entry)
                replaced = True
            continue
        out.append(line)
    if not replaced:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(entry)
    paths.write_private(path, "".join(out))


def store_in_env(name: str, value: str) -> str:
    """Fallback for users without a vault: write ``NAME=value`` into the profile's .env (0600).

    Only ``WPM_*_APP_PASSWORD`` keys are ever written; only that one key is added or replaced.
    Returns a human description of where it went.
    """
    if not registry.ENV_NAME_RE.match(name or ""):
        raise CredentialError("refusing to write a key that is not WPM_<SITE>_APP_PASSWORD")
    password = _clean_password(value)
    if any(c in password for c in "\"'`$\\#= "):
        # Application Passwords are alphanumeric (spaces are cosmetic and stripped below).
        password = password.replace(" ", "")
        if any(c in password for c in "\"'`$\\#="):
            raise CredentialError("that does not look like a WordPress Application Password")
    writer = _env_writer_override or _hermes_env_writer() or _builtin_env_write
    try:
        writer(name, password)
        if writer is _builtin_env_write:
            _written[name] = password
    except ValueError as exc:
        raise CredentialError(redact.scrub(str(exc))[:300]) from exc
    except OSError as exc:
        raise CredentialError(f"could not write the .env file: {type(exc).__name__}") from exc
    env_path = paths.env_file()
    try:
        if env_path.exists() and (os.stat(env_path).st_mode & 0o077):
            os.chmod(env_path, 0o600)
    except OSError:
        pass
    redact.remember(password)
    return f"stored in the Hermes .env as {name} (file mode 0600)"
