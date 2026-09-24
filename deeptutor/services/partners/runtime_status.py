"""Shared, credential-free Partner runtime status."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from deeptutor.partners.config.paths import get_data_dir

CHANNEL_RUNTIME_STALE_SECONDS = 30.0
CHANNEL_STARTUP_TIMEOUT_SECONDS = 60.0


def sanitize_channel_runtime(value: Any) -> dict[str, dict[str, Any]]:
    """Project listener state only; channel config and plugin extras never persist."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for name, entry in value.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        raw_setup = entry.get("setup")
        setup: dict[str, Any] = {}
        if isinstance(raw_setup, dict):
            for key in ("status", "message", "qr_payload", "error_code"):
                if isinstance(raw_setup.get(key), str):
                    setup[key] = raw_setup[key]
            if isinstance(raw_setup.get("retryable"), bool):
                setup["retryable"] = raw_setup["retryable"]
        result[name] = {
            "enabled": bool(entry.get("enabled")),
            "running": bool(entry.get("running")),
            "setup": setup,
        }
        if isinstance(entry.get("setup_updated_at"), (int, float)):
            result[name]["setup_updated_at"] = entry["setup_updated_at"]
    return result


class PartnerRuntimeStatusRepository:
    """WAL SQLite projection written by the leader and read by every worker."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or (get_data_dir() / "_runtime" / "status.sqlite3")).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS partner_runtime_status (
                    partner_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    running INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    started_at TEXT,
                    last_reload_error TEXT,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def set(
        self,
        partner_id: str,
        *,
        owner_id: str,
        running: bool,
        state: str,
        payload: dict[str, Any] | None = None,
        started_at: str | None = None,
        last_reload_error: str | None = None,
        heartbeat: bool = False,
    ) -> dict[str, Any]:
        now = time.time()
        updated_at = now
        safe_payload = dict(payload or {})
        safe_payload.pop("channels", None)
        safe_payload["channel_runtime"] = sanitize_channel_runtime(
            safe_payload.get("channel_runtime")
        )
        previous = self.get(partner_id) if heartbeat else None
        if state == "control_failed" and not heartbeat:
            # A follower timing out knows only that the command failed. It
            # cannot renew the leader's liveness lease with its own clock.
            updated_at = float(safe_payload.get("runtime_updated_at") or 0)
        # Heartbeats refresh liveness, never acknowledge an unprocessed command.
        control_updated_at = (
            previous.get("runtime_control_updated_at", previous.get("runtime_updated_at"))
            if previous
            else now
        )
        if heartbeat and previous and previous.get("runtime_state") == "control_failed":
            state = "control_failed"
            last_reload_error = previous.get("last_reload_error")
        safe_payload.update(
            {
                "partner_id": partner_id,
                "runtime_owner_id": owner_id,
                "running": bool(running),
                "runtime_state": state,
                "started_at": started_at,
                "last_reload_error": last_reload_error,
                "runtime_updated_at": updated_at,
                "runtime_control_updated_at": control_updated_at,
            }
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO partner_runtime_status(
                    partner_id, owner_id, running, state, started_at,
                    last_reload_error, payload, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(partner_id) DO UPDATE SET
                    owner_id=excluded.owner_id,
                    running=excluded.running,
                    state=excluded.state,
                    started_at=excluded.started_at,
                    last_reload_error=excluded.last_reload_error,
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                (
                    partner_id,
                    owner_id,
                    int(running),
                    state,
                    started_at,
                    last_reload_error,
                    json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":")),
                    updated_at,
                ),
            )
        return safe_payload

    def get(self, partner_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM partner_runtime_status WHERE partner_id=?",
                (partner_id,),
            ).fetchone()
        return json.loads(str(row[0])) if row else None

    def list(self) -> dict[str, dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT partner_id, payload FROM partner_runtime_status"
            ).fetchall()
        return {str(row[0]): json.loads(str(row[1])) for row in rows}

    def delete(self, partner_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM partner_runtime_status WHERE partner_id=?", (partner_id,)
            )


_repository: PartnerRuntimeStatusRepository | None = None


def get_partner_runtime_status_repository() -> PartnerRuntimeStatusRepository:
    global _repository
    if _repository is None:
        _repository = PartnerRuntimeStatusRepository()
    return _repository


__all__ = [
    "PartnerRuntimeStatusRepository",
    "get_partner_runtime_status_repository",
]
