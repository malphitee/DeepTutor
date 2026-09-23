"""Admin-issued admission secrets and transactional self-registration."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
from typing import Any
from uuid import uuid4

from . import identity
from .auth_store import (
    IdentityStoreError,
    commit_registration,
    read_json_object,
    write_private_json,
)

CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class InvalidInviteError(ValueError):
    """Missing, malformed, unknown, expired, exhausted, or revoked invite."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_code(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidInviteError("A valid invitation code is required")
    normalized = value.strip().upper().replace("-", "").translate(str.maketrans("OIL", "011"))
    if len(normalized) != 12 or any(char not in CROCKFORD_ALPHABET for char in normalized):
        raise InvalidInviteError("A valid invitation code is required")
    return normalized


def _code_hash(value: str) -> str:
    return hashlib.sha256(normalize_code(value).encode("ascii")).hexdigest()


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise IdentityStoreError("Invalid invite timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise IdentityStoreError("Invalid invite timestamp") from exc
    if parsed.tzinfo is None:
        raise IdentityStoreError("Invite timestamp requires a timezone")
    return parsed.astimezone(timezone.utc)


def _load_records() -> dict[str, dict[str, Any]]:
    records = read_json_object(identity.AUTH_DIR / "invites.json")
    seen_hashes: set[str] = set()
    for invite_id, record in records.items():
        if not isinstance(record, dict) or not {
            "id",
            "code_hash",
            "code_hint",
            "created_by",
            "created_at",
            "expires_at",
            "max_uses",
            "used_count",
            "note",
            "revoked_at",
            "revoked_by",
            "redemptions",
        }.issubset(record):
            raise IdentityStoreError("Invalid invite record")
        code_hash = record.get("code_hash")
        max_uses = record.get("max_uses")
        used_count = record.get("used_count")
        redemptions = record.get("redemptions")
        if (
            not invite_id.startswith("inv_")
            or record.get("id") != invite_id
            or not isinstance(code_hash, str)
            or not _HASH_RE.fullmatch(code_hash)
            or code_hash in seen_hashes
            or type(max_uses) is not int
            or not 1 <= max_uses <= 1000
            or type(used_count) is not int
            or not 0 <= used_count <= max_uses
            or not isinstance(redemptions, list)
            or len(redemptions) != used_count
            or not isinstance(record.get("note"), str)
            or len(record["note"]) > 200
            or not isinstance(record.get("created_by"), str)
            or (record.get("revoked_by") is not None and not isinstance(record["revoked_by"], str))
            or not isinstance(record.get("code_hint"), str)
            or len(record["code_hint"]) != 4
            or any(char not in CROCKFORD_ALPHABET for char in record["code_hint"])
        ):
            raise IdentityStoreError("Invalid invite record")
        seen_hashes.add(code_hash)
        _parse_time(record.get("created_at"))
        if record.get("expires_at") is not None:
            _parse_time(record["expires_at"])
        if record.get("revoked_at") is not None:
            _parse_time(record["revoked_at"])
        redeemed_ids: set[str] = set()
        for redemption in redemptions:
            if (
                not isinstance(redemption, dict)
                or not isinstance(redemption.get("user_id"), str)
                or not redemption["user_id"]
                or redemption["user_id"] in redeemed_ids
                or not isinstance(redemption.get("username"), str)
                or not redemption["username"]
            ):
                raise IdentityStoreError("Invalid invite redemption")
            redeemed_ids.add(redemption["user_id"])
            _parse_time(redemption.get("redeemed_at"))
    return records


def _status(record: dict[str, Any], now: datetime) -> str:
    if record["revoked_at"] is not None:
        return "revoked"
    if record["expires_at"] is not None and _parse_time(record["expires_at"]) <= now:
        return "expired"
    if record["used_count"] >= record["max_uses"]:
        return "exhausted"
    return "active"


def _public_record(record: dict[str, Any], now: datetime) -> dict[str, Any]:
    # An explicit allowlist keeps accidental future secret fields private.
    result = {
        key: deepcopy(record[key])
        for key in (
            "id",
            "created_by",
            "created_at",
            "expires_at",
            "max_uses",
            "used_count",
            "note",
            "revoked_at",
            "revoked_by",
            "redemptions",
            "code_hint",
        )
    }
    result["status"] = _status(record, now)
    result["remaining_uses"] = record["max_uses"] - record["used_count"]
    return result


def create_invites(
    created_by: str,
    batch_count: int = 1,
    max_uses: int = 1,
    expires_in_days: int | None = 7,
    note: str = "",
) -> list[dict[str, Any]]:
    """Issue a batch atomically, returning plaintext codes only on this call."""
    if type(batch_count) is not int or not 1 <= batch_count <= 100:
        raise ValueError("batch_count must be between 1 and 100")
    if type(max_uses) is not int or not 1 <= max_uses <= 1000:
        raise ValueError("max_uses must be between 1 and 1000")
    if expires_in_days is not None and (
        type(expires_in_days) is not int or not 1 <= expires_in_days <= 365
    ):
        raise ValueError("expires_in_days must be between 1 and 365 or null")
    if not isinstance(note, str) or len(note) > 200:
        raise ValueError("note must be at most 200 characters")
    if not isinstance(created_by, str) or not created_by:
        raise ValueError("created_by is required")
    with identity.auth_store_transaction():
        records = _load_records()
        hashes = {record["code_hash"] for record in records.values()}
        now = utc_now()
        expires_at = (
            (now + timedelta(days=expires_in_days)).isoformat() if expires_in_days else None
        )
        issued: list[dict[str, Any]] = []
        for _ in range(batch_count):
            while True:
                code = "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(12))
                code_hash = _code_hash(code)
                if code_hash not in hashes:
                    hashes.add(code_hash)
                    break
            invite_id = f"inv_{uuid4().hex}"
            record = {
                "id": invite_id,
                "code_hash": code_hash,
                "code_hint": code[-4:],
                "created_by": created_by,
                "created_at": now.isoformat(),
                "expires_at": expires_at,
                "max_uses": max_uses,
                "used_count": 0,
                "note": note,
                "revoked_at": None,
                "revoked_by": None,
                "redemptions": [],
            }
            records[invite_id] = record
            issued.append(
                {
                    **_public_record(record, now),
                    "code": "-".join(code[index : index + 4] for index in range(0, 12, 4)),
                }
            )
        write_private_json(identity.AUTH_DIR / "invites.json", records)
        return issued


def list_invites(offset: int = 0, limit: int = 50) -> dict[str, Any]:
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be nonnegative")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    with identity.auth_store_transaction():
        records = _load_records()
        ordered = sorted(records.values(), key=lambda record: record["created_at"], reverse=True)
        now = utc_now()
        return {
            "items": [_public_record(record, now) for record in ordered[offset : offset + limit]],
            "total": len(ordered),
        }


def revoke_invite(invite_id: str, revoked_by: str) -> dict[str, Any] | None:
    with identity.auth_store_transaction():
        records = _load_records()
        record = records.get(invite_id)
        if record is None:
            return None
        now = utc_now()
        if record["revoked_at"] is None:
            record["revoked_at"] = now.isoformat()
            record["revoked_by"] = revoked_by
            write_private_json(identity.AUTH_DIR / "invites.json", records)
        return _public_record(record, now)


def validate_registration_invite(invite_code: str | None = None) -> bool:
    """Cheap preflight before password hashing; the final transaction rechecks.

    Return whether the instance is still in bootstrap state. This result is
    advisory and must never replace the locked check in register_user().
    """
    with identity.auth_store_transaction():
        first_user = identity.is_bootstrap_available()
        records = _load_records()
        if first_user:
            return True
        code_hash = _code_hash(invite_code or "")
        matched = next(
            (record for record in records.values() if record["code_hash"] == code_hash), None
        )
        if matched is None or _status(matched, utc_now()) != "active":
            raise InvalidInviteError("A valid invitation code is required")
        return False


def register_user(
    username: str, hashed_password: str, invite_code: str | None = None
) -> dict[str, Any]:
    """Create the bootstrap admin or atomically redeem one use for a new user.

    A committed account remains created if its HTTP response is lost. Retrying
    never charges a second use: it conflicts for a still-valid code, or returns
    the uniform invite error for an exhausted code. User deletion never removes
    redemption history or refunds a use.
    """
    with identity.auth_store_transaction():
        users = identity.load_users()
        env_username, _ = identity._env_bootstrap_admin()
        first_user = not users and not env_username
        records = _load_records()
        matched = None
        now = utc_now()
        if not first_user:
            code_hash = _code_hash(invite_code or "")
            matched = next(
                (record for record in records.values() if record["code_hash"] == code_hash), None
            )
            if matched is None or _status(matched, now) != "active":
                raise InvalidInviteError("A valid invitation code is required")
        if username in users or (env_username and username == env_username):
            raise identity.UserAlreadyExistsError("Username already taken")
        user = identity._new_user_record(
            hashed_password, role="admin" if first_user else "user", preset="standard"
        )
        users[username] = user
        if matched is not None:
            matched["used_count"] += 1
            matched["redemptions"].append(
                {
                    "user_id": user["id"],
                    "username": username,
                    "redeemed_at": now.isoformat(),
                }
            )
        commit_registration(identity.AUTH_DIR, identity.USERS_FILE, users, records)
        return {**deepcopy(user), "is_first_user": first_user}
