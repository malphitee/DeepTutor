"""Private JSON persistence and recoverable identity/invite transactions.

The journal is the commit point. Once it is durable, every store entry point
rolls it forward before exposing or changing either file. This is a single
process protocol: the reentrant lock must be shared by every identity writer.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Iterator
from uuid import uuid4


class IdentityStoreError(RuntimeError):
    """Identity data is unavailable; callers must not assume an empty store."""


class UserAlreadyExistsError(ValueError):
    """A create-only operation encountered an existing or reserved username."""


AUTH_STORE_LOCK = threading.RLock()
JOURNAL_FILENAME = "registration-journal.json"


def read_json_object(path: Path, *, missing_ok: bool = True) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        if missing_ok:
            return {}
        raise IdentityStoreError(f"Missing identity store: {path.name}") from exc
    except (OSError, ValueError) as exc:
        raise IdentityStoreError(f"Cannot read identity store: {path.name}") from exc
    if not isinstance(value, dict):
        raise IdentityStoreError(f"Invalid identity store: {path.name}")
    return value


def _sync_directory(directory: Path) -> None:
    # Windows does not support opening directories this way. File contents
    # remain fsynced and the replacement itself is atomic on that platform.
    if os.name == "nt":  # pragma: no cover - platform specific
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically replace a private file, syncing contents and directory entry."""
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    except (OSError, TypeError, ValueError) as exc:
        raise IdentityStoreError(f"Cannot write identity store: {path.name}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def recover_registration(auth_dir: Path, users_file: Path) -> None:
    """Apply an already committed transaction; callers hold AUTH_STORE_LOCK."""
    journal_path = auth_dir / JOURNAL_FILENAME
    journal = read_json_object(journal_path)
    if not journal:
        if journal_path.exists():
            raise IdentityStoreError("Invalid registration journal")
        return
    if (
        type(journal.get("version")) is not int
        or journal["version"] != 1
        or not isinstance(journal.get("transaction_id"), str)
        or len(journal["transaction_id"]) != 32
        or any(char not in "0123456789abcdef" for char in journal["transaction_id"])
        or not isinstance(journal.get("users"), dict)
        or not journal["users"]
        or not isinstance(journal.get("invites"), dict)
        or not isinstance(journal.get("snapshot_sha256"), str)
        or len(journal["snapshot_sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in journal["snapshot_sha256"])
    ):
        raise IdentityStoreError("Invalid registration journal")
    if not hmac.compare_digest(
        journal["snapshot_sha256"], _snapshot_digest(journal["users"], journal["invites"])
    ):
        raise IdentityStoreError("Registration journal checksum mismatch")
    # Replacing the complete snapshots is idempotent. No other writer may
    # enter until both replacements and journal removal have completed.
    write_private_json(users_file, journal["users"])
    write_private_json(auth_dir / "invites.json", journal["invites"])
    try:
        journal_path.unlink()
        _sync_directory(auth_dir)
    except OSError as exc:
        raise IdentityStoreError("Cannot finish registration recovery") from exc


@contextmanager
def transaction(auth_dir: Path, users_file: Path) -> Iterator[None]:
    with AUTH_STORE_LOCK:
        recover_registration(auth_dir, users_file)
        yield


def _snapshot_digest(users: dict[str, Any], invites: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"users": users, "invites": invites},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def commit_registration(
    auth_dir: Path,
    users_file: Path,
    users: dict[str, Any],
    invites: dict[str, Any],
) -> None:
    """Commit both snapshots, retaining a recoverable journal on failure.

    Callers hold AUTH_STORE_LOCK. An exception after the journal replacement
    means the result may already be committed; it must never be compensated
    by returning an invite use or deleting the newly created account.
    """
    write_private_json(
        auth_dir / JOURNAL_FILENAME,
        {
            "version": 1,
            "transaction_id": uuid4().hex,
            "users": users,
            "invites": invites,
            "snapshot_sha256": _snapshot_digest(users, invites),
        },
    )
    recover_registration(auth_dir, users_file)
