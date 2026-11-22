"""Encryption at rest for endpoint credentials, plus secret redaction helpers."""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings

SECRET_FIELDS = {"token", "api_key", "client_secret", "password", "secret"}
MASK = "••••••"


def _fernet() -> Fernet:
    digest = hashlib.sha256(("switchyard-enc:" + get_settings().secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_json(data: dict[str, Any]) -> str:
    if not data:
        return ""
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(blob: str) -> dict[str, Any]:
    if not blob:
        return {}
    try:
        return json.loads(_fernet().decrypt(blob.encode()))
    except InvalidToken as exc:  # SECRET_KEY rotated without re-encrypting
        raise RuntimeError("Stored credentials cannot be decrypted (was SECRET_KEY changed?)") from exc


def mask_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Safe-to-display copy: secret values replaced by a mask (or empty if unset)."""
    return {k: (MASK if v else "") if k in SECRET_FIELDS else v for k, v in cfg.items()}


def merge_config(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Apply an edit from the UI: a masked secret means 'keep the stored value'."""
    out = dict(new)
    for k in SECRET_FIELDS:
        if out.get(k) == MASK:
            out[k] = old.get(k, "")
    return out


REDACT_KEYS = {"authorization", "password", "secret", "client_secret", "token", "access_token",
               "refresh_token", "api_key", "x-api-key", "cookie", "set-cookie"}


def redact(obj: Any) -> Any:
    """Recursively redact obviously-secret keys (used before writing audit details)."""
    if isinstance(obj, dict):
        return {k: ("[redacted]" if str(k).lower() in REDACT_KEYS else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj
