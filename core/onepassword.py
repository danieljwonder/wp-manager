"""Explicit opt-in item creation through the configured trusted 1Password CLI.

Only metadata is enumerated. Secrets travel on stdin, never argv or disk. CLI
output/errors are untrusted and never included in errors or logs.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import subprocess

from .credentials import CredentialError, OP_TIMEOUT

ID_RE = re.compile(r"^[a-z0-9]{26}$")


def _run(binary: str, args: list[str], payload: dict | None = None) -> str:
    if not isinstance(binary, str) or not re.fullmatch(r"[A-Za-z0-9_./\\:-]{1,255}", binary) or binary.startswith("-"):
        raise CredentialError("invalid op_binary setting")
    exe = shutil.which(binary) if os.sep not in binary else binary
    if not exe:
        raise CredentialError("1Password CLI unavailable; choose another storage option explicitly")
    try:
        proc = subprocess.run([exe, *args], input=json.dumps(payload) if payload is not None else "",
                              capture_output=True, text=True, timeout=OP_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise CredentialError("1Password CLI unavailable or timed out; no local fallback was used") from None
    if proc.returncode:
        raise CredentialError("1Password operation failed; check CLI access and vault permissions; no local fallback was used")
    return proc.stdout


def vaults(binary: str) -> list[dict]:
    try:
        data = json.loads(_run(binary, ["vault", "list", "--format=json"]))
        if not isinstance(data, list):
            raise ValueError()
        out = []
        for v in data:
            if not isinstance(v, dict) or not ID_RE.fullmatch(str(v.get("id", ""))) or not isinstance(v.get("name"), str):
                raise ValueError()
            out.append({"id": v["id"], "name": v["name"]})
        return out
    except (ValueError, TypeError):
        raise CredentialError("1Password returned invalid vault metadata") from None


def options(binary: str) -> dict:
    try:
        available = vaults(binary)
        if available:
            return {"available": True, "vaults": available}
    except CredentialError:
        pass
    return {"available": False, "vaults": [],
            "reason": "1Password CLI or vault access unavailable. Retry or explicitly choose another storage option."}


def create(binary: str, vault_id: str, site: dict, password: str) -> tuple[dict, str]:
    if not isinstance(vault_id, str) or not ID_RE.fullmatch(vault_id):
        raise CredentialError("choose an explicit 1Password vault")
    available = vaults(binary)
    vault = next((v for v in available if v["id"] == vault_id), None)
    if vault is None:
        raise CredentialError("the selected 1Password vault is not accessible")
    payload = {"title": f"WordPress: {site['label']}", "category": "LOGIN",
               "vault": {"id": vault_id}, "urls": [{"href": site["url"], "primary": True}],
               "fields": [{"id": "username", "type": "STRING", "purpose": "USERNAME", "value": site["username"]},
                          {"id": "password", "type": "CONCEALED", "purpose": "PASSWORD", "value": password}]}
    # item create always creates a new item; never edit or select an item by title.
    raw = _run(binary, ["item", "create", "-", "--format=json", "--vault", vault_id], payload)
    try:
        item = json.loads(raw)
        item_id = item["id"]
        if not isinstance(item_id, str) or not ID_RE.fullmatch(item_id) or item["vault"]["id"] != vault_id:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise CredentialError("1Password item response invalid; registry unchanged. A new item may need manual removal") from None
    ref = f"op://{vault_id}/{item_id}/password"
    readback = _run(binary, ["read", "--no-newline", ref])
    if not hmac.compare_digest(readback.encode(), password.encode()):
        raise CredentialError("1Password read-back mismatch; registry unchanged. Remove the newly created item manually")
    return {"op": ref}, f"stored in 1Password vault {vault['name']} ({ref})"
