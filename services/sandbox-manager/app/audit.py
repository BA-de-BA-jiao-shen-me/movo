"""Sandbox audit log: one JSON line per lifecycle/security event."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

EVENTS = {"sandbox.create", "sandbox.exit", "sandbox.fallback_runc", "sandbox.denied",
          "sandbox.probe", "egress.denied", "plugin.install"}
_lock = threading.Lock()
_log = logging.getLogger("movo.audit")


def audit_path() -> Path:
    return Path(os.environ.get("MOVO_SANDBOX_AUDIT_LOG", "/state/audit.log"))


def emit(event: str, **fields) -> dict:
    if event not in EVENTS:
        raise ValueError(f"Unknown audit event {event!r}")
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event}
    record.update({k: v for k, v in fields.items() if v is not None})
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    try:
        path = audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        _log.warning("audit log unavailable; falling back to stderr")
    _log.info(line)
    return record
