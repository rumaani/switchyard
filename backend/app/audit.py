"""Append-only audit trail. Every security-relevant action goes through `record`."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent
from .security.crypto import redact

log = logging.getLogger("switchyard.audit")

MAX_DETAIL_CHARS = 8000


def record(db: Session, actor: str, action: str, target: str = "", outcome: str = "ok",
           detail: dict[str, Any] | None = None, ip: str = "") -> None:
    detail = redact(detail or {})
    text = json.dumps(detail, default=str)
    if len(text) > MAX_DETAIL_CHARS:  # keep the trail light; full payloads live on the run step
        detail = {"truncated": True, "preview": text[:MAX_DETAIL_CHARS]}
    db.add(AuditEvent(actor=actor or "system", action=action, target=target, outcome=outcome, detail=detail, ip=ip))
    # one JSON line per event on stdout too, so a log shipper can forward it to the SIEM
    log.info(json.dumps({"actor": actor, "action": action, "target": target, "outcome": outcome,
                         "detail": detail, "ip": ip}, default=str))
