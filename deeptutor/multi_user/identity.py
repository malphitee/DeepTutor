"""Canonical identity store for the optional multi-user layer."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import secrets
from typing import Any
from uuid import uuid4

from deeptutor.services.file_io import atomic_write_text
from deeptutor.utils.secret_files import write_secret_text

from .auth_store import (
    AUTH_STORE_LOCK,
    IdentityStoreError,
    UserAlreadyExistsError,
    read_json_object,
    transaction,
    write_private_json,
)
from .book_permission import (
    BookPermission,
    canonical_book_permission,
    normalize_book_permission,
    public_permission_dict,
)
from .learner_profile import normalize_profile
from .models import AccountPreset, Role, normalize_role
from .paths import PROJECT_ROOT, SYSTEM_ROOT, migrate_legacy_multi_user_tree

logger = logging.getLogger(__name__)

# All identity and invite operations share one reentrant lock and recover any
# committed registration journal before reading or writing. One process only.
_USERS_WRITE_LOCK = AUTH_STORE_LOCK

AUTH_DIR = SYSTEM_ROOT / "auth"
USERS_FILE = AUTH_DIR / "users.json"
SECRET_FILE = AUTH_DIR / "auth_secret"
REVOKED_USERS_FILE = AUTH_DIR / "revoked_users.json"
LEGACY_USERS_FILE = PROJECT_ROOT / "data" / "user" / "auth_users.json"
LEGACY_SECRET_FILE = PROJECT_ROOT / "data" / "user" / "auth_secret"


def auth_store_transaction():
    """Lock both stores and finish any pending registration before accessing them."""
    return transaction(AUTH_DIR, USERS_FILE)


def new_user_id() -> str:
    return f"u_{uuid4().hex}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token_version(value: Any, *, missing: int = 0) -> int:
    """Read a bounded account incarnation counter from legacy JSON."""

    if not isinstance(value, dict) or "token_version" not in value:
        return missing
    try:
        return max(0, int(value.get("token_version", missing) or 0))
    except (TypeError, ValueError):
        # A malformed counter must not make the identity store unreadable. The
        # conservative value is still replaced on the next state mutation.
        return missing


def _canonical_record(
    username: str,
    value: Any,
    *,
    default_role: Role = "user",
) -> dict[str, Any] | None:
    if isinstance(value, str):
        if not value:
            return None
        return {
            "id": new_user_id(),
            "hash": value,
            "role": default_role,
            "token_version": 0,
            "created_at": utc_now(),
            "disabled": False,
            "avatar": "",
        }
    if not isinstance(value, dict):
        return None
    hashed = value.get("hash") or value.get("password_hash") or ""
    if not isinstance(hashed, str) or not hashed:
        return None
    # Only absent fields receive legacy defaults. Invalid explicit roles/presets
    # fail closed instead of silently changing account privileges.
    role = value.get("role", default_role)
    if not isinstance(role, str) or role not in {"admin", "user"}:
        raise IdentityStoreError("Invalid user role")
    preset = value.get("preset", "standard")
    if not isinstance(preset, str) or preset not in {"standard", "learner", "custom"}:
        raise IdentityStoreError("Invalid user preset")
    record = {
        "id": str(value.get("id") or new_user_id()),
        "hash": hashed,
        "role": role,
        # Incremented whenever an account's authentication state changes.  It
        # is part of every locally signed token, so a role/disable/password
        # update invalidates already issued tokens without a token blacklist.
        # Legacy records have no incarnation counter; start them at one so a
        # pre-isolation token with an implicit version of zero is rejected.
        "token_version": _token_version(value, missing=1),
        "created_at": str(value.get("created_at") or utc_now()),
        "disabled": bool(value.get("disabled", False)),
        "avatar": str(value.get("avatar") or ""),
        "preset": preset,
    }
    if "book_permission" in value:
        record["book_permission"] = canonical_book_permission(value.get("book_permission"))
    if "learner_profile" in value:
        record["learner_profile"] = normalize_profile(value.get("learner_profile"))
    return record


def _read_json(path: Path) -> dict[str, Any]:
    return read_json_object(path)


def _write_users(users: dict[str, dict[str, Any]]) -> None:
    write_private_json(USERS_FILE, users)


def _revoke_user_runtime(user_id: str) -> None:
    """Best-effort local fan-out after an authentication state mutation."""

    if not user_id:
        return
    try:
        from .revocation import revoke

        revoke(user_id)
    except Exception:
        logger.exception("Could not revoke live resources for user %s", user_id)


def _record_deleted_identity(username: str, user_id: str) -> None:
    """Keep a tombstone so a token survives neither delete nor re-create."""

    if not username:
        return
    revoked_file = AUTH_DIR / "revoked_users.json"
    tombstones = _read_json(revoked_file)
    tombstones[str(username)] = {"id": str(user_id or ""), "revoked_at": utc_now()}
    revoked_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(revoked_file, json.dumps(tombstones, indent=2, ensure_ascii=False))


def deleted_identity_revoked(username: str, user_id: str = "") -> bool:
    """Whether a token refers to an account deleted in the past."""

    with auth_store_transaction():
        row = _read_json(AUTH_DIR / "revoked_users.json").get(str(username))
    if not isinstance(row, dict):
        return False
    old_id = str(row.get("id") or "")
    return not user_id or not old_id or str(user_id) == old_id


def _migrate_legacy_users() -> dict[str, dict[str, Any]] | None:
    if USERS_FILE.exists() or not LEGACY_USERS_FILE.exists():
        return None
    legacy = _read_json(LEGACY_USERS_FILE)
    users: dict[str, dict[str, Any]] = {}
    for username, value in legacy.items():
        role: Role = "admin" if not users else "user"
        if isinstance(value, dict):
            role = normalize_role(str(value.get("role") or ""), role)  # type: ignore[assignment]
        record = _canonical_record(username, value, default_role=role)
        if record is None:
            raise IdentityStoreError("Invalid legacy user record")
        users[str(username)] = record
    if users:
        _write_users(users)
        logger.info("Migrated auth users from %s to %s", LEGACY_USERS_FILE, USERS_FILE)
        return users
    return None


def _migrate_secret() -> None:
    if SECRET_FILE.exists() or not LEGACY_SECRET_FILE.exists():
        return
    try:
        secret = LEGACY_SECRET_FILE.read_text(encoding="utf-8").strip()
        if secret:
            write_secret_text(SECRET_FILE, secret)
            logger.info("Migrated auth secret from %s to %s", LEGACY_SECRET_FILE, SECRET_FILE)
    except Exception as exc:
        logger.warning("Failed to migrate legacy auth secret: %s", exc)


def _env_bootstrap_admin() -> tuple[str, str]:
    """Return ``(username, password_hash)`` for the ``auth.json`` bootstrap admin.

    Both halves are required: the shipped default seeds a username with an
    empty hash, which cannot authenticate and therefore is not an admin. An
    empty tuple entry means "no bootstrap admin configured".

    :mod:`deeptutor.services.auth` is imported lazily because it imports this
    module. Its resolved globals — rather than a fresh settings read — are the
    source of truth on purpose: they are exactly the credentials
    ``authenticate()`` accepts, so the promotion gate in :func:`save_user` and
    the login path can never disagree about whether an admin already exists.
    """
    try:
        from deeptutor.services import auth as auth_service

        username = str(getattr(auth_service, "AUTH_USERNAME", "") or "")
        password_hash = str(getattr(auth_service, "AUTH_PASSWORD_HASH", "") or "")
    except Exception as exc:
        raise IdentityStoreError("Cannot resolve bootstrap administrator credentials") from exc
    if not username or not password_hash:
        return "", ""
    return username, password_hash


def _env_admin_record(password_hash: str) -> dict[str, Any]:
    """Build the in-memory record representing the bootstrap admin."""
    return {
        "id": "env-admin",
        "hash": password_hash,
        "role": "admin",
        "token_version": 0,
        "created_at": "",
        "disabled": False,
        "avatar": "",
        "preset": "standard",
    }


def load_users(  # nosec B107 - empty defaults mean "no env fallback supplied".
    env_username: str = "",
    env_password_hash: str = "",
) -> dict[str, dict[str, Any]]:
    """Load canonical users, migrating legacy records and env fallback in memory."""
    with auth_store_transaction():
        return _load_users_locked(env_username, env_password_hash)


def _load_users_locked(env_username: str, env_password_hash: str) -> dict[str, dict[str, Any]]:
    migrate_legacy_multi_user_tree()
    users: dict[str, dict[str, Any]] | None = None
    if USERS_FILE.exists():
        users = _read_json(USERS_FILE)
    else:
        users = _migrate_legacy_users()

    if users is None:
        users = {}

    canonical: dict[str, dict[str, Any]] = {}
    changed = False
    for index, (username, value) in enumerate(users.items()):
        role: Role = "admin" if index == 0 else "user"
        if isinstance(value, dict):
            role = normalize_role(str(value.get("role") or ""), role)  # type: ignore[assignment]
        record = _canonical_record(str(username), value, default_role=role)
        if record is None:
            raise IdentityStoreError("Invalid user record")
        canonical[str(username)] = record
        changed = changed or record != value

    if USERS_FILE.exists() and changed:
        _write_users(canonical)

    # The bootstrap admin is merged in whenever it is configured, not only when
    # the store is empty. Falling back to it only for an empty store locked the
    # operator who bootstrapped the deployment out of their own instance the
    # moment the first real account was written (#849). A stored record with the
    # same username wins, so the account can later be adopted into the store.
    # The merge is deliberately in-memory only; no write path passes the env
    # arguments, so the bootstrap hash is never persisted into ``users.json``
    # where a rotation of ``auth.json`` could no longer supersede it.
    if env_username and env_password_hash and env_username not in canonical:
        merged = {env_username: _env_admin_record(env_password_hash)}
        merged.update(canonical)
        return merged

    return canonical


def _new_user_record(
    hashed_password: str, *, role: Role = "user", preset: AccountPreset = "standard"
) -> dict[str, Any]:
    if not isinstance(hashed_password, str) or not hashed_password:
        raise ValueError("A password hash is required")
    if role not in {"admin", "user"} or preset not in {"standard", "learner", "custom"}:
        raise ValueError("Invalid account role or preset")
    return {
        "id": new_user_id(),
        "hash": hashed_password,
        "role": role,
        "token_version": 0,
        "created_at": utc_now(),
        "disabled": False,
        "avatar": "",
        "preset": preset,
        "book_permission": canonical_book_permission(None),
        "learner_profile": normalize_profile(None),
    }


def is_bootstrap_available() -> bool:
    """Return true only for a readable empty store without a configured admin."""
    with auth_store_transaction():
        users = load_users()
        env_username, _ = _env_bootstrap_admin()
        return not users and not env_username


def create_user(
    username: str,
    hashed_password: str,
    role: Role = "user",
    preset: AccountPreset = "standard",
) -> dict[str, Any]:
    """Create one account, never overwriting an existing or bootstrap identity."""
    with auth_store_transaction():
        users = load_users()
        env_username, _ = _env_bootstrap_admin()
        if username in users or (env_username and username == env_username):
            raise UserAlreadyExistsError("Username already taken")
        effective_role: Role = role if users or env_username else "admin"
        record = _new_user_record(hashed_password, role=effective_role, preset=preset)
        users[username] = record
        _write_users(users)
        return deepcopy(record)


def save_user(
    username: str,
    hashed_password: str,
    role: Role = "user",
    preset: AccountPreset = "standard",
) -> dict[str, Any]:
    # Read-modify-write must be atomic so concurrent first-time registrations
    # cannot each see an empty store and each promote themselves to admin.
    with auth_store_transaction():
        # Called without the env arguments on purpose: ``users`` is written back
        # to disk below, and the bootstrap admin must stay an in-memory overlay.
        users = load_users()
        env_username, _ = _env_bootstrap_admin()
        # A configured bootstrap admin counts as an existing account, so the
        # first account an operator creates from /admin/users is not silently
        # promoted — that endpoint documents role="user" (#849). Re-saving the
        # bootstrap admin's own username adopts it into the store instead, and
        # must keep the admin role.
        account_exists = bool(users) or (bool(env_username) and env_username != username)
        effective_role: Role = role if account_exists else "admin"
        existing = users.get(username) or {}
        effective_preset = str(existing.get("preset") or preset or "standard")
        if effective_preset not in {"standard", "learner", "custom"}:
            effective_preset = preset
        record = {
            "id": str(existing.get("id") or new_user_id()),
            "hash": hashed_password,
            "role": effective_role,
            "token_version": _token_version(existing),
            "created_at": str(existing.get("created_at") or utc_now()),
            "disabled": bool(existing.get("disabled", False)),
            "avatar": str(existing.get("avatar") or ""),
            "preset": effective_preset,
            "book_permission": canonical_book_permission(existing.get("book_permission")),
            "learner_profile": normalize_profile(existing.get("learner_profile")),
        }
        credentials_changed = bool(existing) and (
            existing.get("hash") != hashed_password or existing.get("role") != effective_role
        )
        if credentials_changed:
            record["token_version"] += 1
        users[username] = record
        _write_users(users)
    if credentials_changed:
        _revoke_user_runtime(record["id"])
    return record


def list_user_info(  # nosec B107 - empty defaults mean "no env fallback supplied".
    env_username: str = "",
    env_password_hash: str = "",
) -> list[dict[str, Any]]:
    return [
        {
            "id": record.get("id", ""),
            "username": username,
            "role": record.get("role", "user"),
            "created_at": record.get("created_at", ""),
            "disabled": bool(record.get("disabled", False)),
            "avatar": str(record.get("avatar") or ""),
            "preset": str(record.get("preset") or "standard"),
            "book_permission": public_permission_dict(
                normalize_book_permission(record.get("book_permission"))
            ),
        }
        for username, record in load_users(env_username, env_password_hash).items()
    ]


def get_user(username: str) -> dict[str, Any] | None:
    return load_users().get(username)


def get_user_by_id(user_id: str) -> tuple[str, dict[str, Any]] | None:
    for username, record in load_users().items():
        if str(record.get("id") or "") == user_id:
            return username, record
    return None


def get_learner_profile(username: str) -> dict[str, Any] | None:
    """Return a learner account's structured profile, if present."""
    record = get_user(username)
    if record is None or str(record.get("preset") or "standard") != "learner":
        return None
    return normalize_profile(record.get("learner_profile"))


def set_learner_profile(username: str, profile: dict[str, Any] | None) -> dict[str, Any] | None:
    """Atomically replace one ordinary user's structured learner profile."""
    with auth_store_transaction():
        users = load_users()
        record = users.get(username)
        if (
            record is None
            or str(record.get("role") or "user") != "user"
            or str(record.get("preset") or "standard") != "learner"
        ):
            return None
        record["learner_profile"] = normalize_profile(profile)
        _write_users(users)
        return record["learner_profile"]


def set_book_permission(username: str, permission: BookPermission) -> bool:
    """Atomically replace one ordinary user's shared-book permission."""

    with auth_store_transaction():
        users = load_users()
        record = users.get(username)
        if record is None:
            return False
        record["book_permission"] = public_permission_dict(permission)
        _write_users(users)
    return True


def remove_book_permission_overrides(book_id: str) -> list[str]:
    """Remove a deleted shared book from every explicit ACL.

    Returns affected user ids for the deletion audit summary.
    """

    affected: list[str] = []
    with auth_store_transaction():
        users = load_users()
        changed = False
        for record in users.values():
            permission = normalize_book_permission(record.get("book_permission"))
            books = permission.books_dict()
            if book_id not in books:
                continue
            books.pop(book_id)
            record["book_permission"] = public_permission_dict(
                BookPermission(
                    create=permission.create,
                    default=permission.default,
                    books=tuple(books.items()),
                )
            )
            affected.append(str(record.get("id") or ""))
            changed = True
        if changed:
            _write_users(users)
    return affected


def delete_user(username: str) -> bool:
    with auth_store_transaction():
        users = load_users()
        record = users.get(username)
        if record is None:
            return False
        user_id = str(record.get("id") or "")
        users.pop(username, None)
        _record_deleted_identity(username, user_id)
        _write_users(users)
    _revoke_user_runtime(user_id)
    try:
        from .guardians import revoke_relationships_for_user

        # Route guards also revalidate both accounts, so a rare cleanup failure
        # can never make a deleted-user relationship usable.
        revoke_relationships_for_user(user_id, reason="user_deleted")
    except Exception:
        logger.exception("Could not revoke guardian relationships after user deletion")
    return True


def set_password(
    username: str,
    hashed_password: str,
    *,
    expected_user_id: str | None = None,
    expected_token_version: int | None = None,
    expected_hash: str | None = None,
) -> dict[str, Any] | None:
    """Replace a password, optionally only if its authenticated snapshot still matches.

    Self-service callers verify and hash passwords outside the store lock, then
    supply all three expectations to reject concurrent revocation, replacement,
    or another password change. Existing administrative reset callers need no
    snapshot and can still reset a disabled account's password.
    """
    with auth_store_transaction():
        users = load_users()
        record = users.get(username)
        if record is None:
            return None
        if expected_user_id is not None and (
            record.get("id") != expected_user_id or record.get("disabled", False)
        ):
            return None
        if expected_token_version is not None and _token_version(record) != expected_token_version:
            return None
        if expected_hash is not None and record.get("hash") != expected_hash:
            return None
        record["hash"] = hashed_password
        record["token_version"] = _token_version(record) + 1
        _write_users(users)
        updated = deepcopy(record)
    _revoke_user_runtime(str(updated.get("id") or ""))
    return updated


def set_avatar(username: str, avatar: str) -> bool:
    """Update the avatar marker for an existing user. Returns True on success."""
    with auth_store_transaction():
        users = load_users()
        if username not in users:
            return False
        users[username]["avatar"] = avatar
        _write_users(users)
    return True


# ---------------------------------------------------------------------------
# Avatar image files — stored next to the user store, keyed by user id
# ---------------------------------------------------------------------------

# Extensions are derived from server-side content sniffing, never from the
# uploaded filename, so this list is also the full set of files we may serve.
AVATAR_EXTENSIONS = ("png", "jpg", "webp")


def _avatar_dir() -> Path:
    # Resolved lazily so tests that monkeypatch AUTH_DIR keep avatars isolated.
    return AUTH_DIR / "avatars"


def get_avatar_file(user_id: str) -> Path | None:
    """Return the stored avatar image for ``user_id``, or None."""
    for ext in AVATAR_EXTENSIONS:
        candidate = _avatar_dir() / f"{user_id}.{ext}"
        if candidate.is_file():
            return candidate
    return None


def save_avatar_file(user_id: str, data: bytes, ext: str) -> Path:
    """Atomically persist an avatar image, replacing any previous one."""
    if ext not in AVATAR_EXTENSIONS:
        raise ValueError(f"Unsupported avatar extension: {ext!r}")
    directory = _avatar_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{user_id}.{ext}"
    tmp = directory / f"{user_id}.{ext}.tmp"
    tmp.write_bytes(data)
    tmp.replace(target)
    # A re-upload may change the extension; drop stale siblings.
    for other in AVATAR_EXTENSIONS:
        if other != ext:
            (directory / f"{user_id}.{other}").unlink(missing_ok=True)
    return target


def delete_avatar_file(user_id: str) -> None:
    for ext in AVATAR_EXTENSIONS:
        (_avatar_dir() / f"{user_id}.{ext}").unlink(missing_ok=True)


def set_role(username: str, role: Role) -> bool:
    if role not in {"admin", "user"}:
        raise ValueError("role must be 'admin' or 'user'")
    with auth_store_transaction():
        users = load_users()
        if username not in users:
            return False
        users[username]["role"] = role
        users[username]["token_version"] = _token_version(users[username]) + 1
        user_id = str(users[username].get("id") or "")
        _write_users(users)
    _revoke_user_runtime(user_id)
    return True


def set_disabled(username: str, disabled: bool) -> bool:
    """Enable or disable an account and revoke previously issued tokens."""
    with auth_store_transaction():
        users = load_users()
        record = users.get(username)
        if record is None:
            return False
        disabled = bool(disabled)
        if bool(record.get("disabled", False)) != disabled:
            record["disabled"] = disabled
            record["token_version"] = _token_version(record) + 1
            user_id = str(record.get("id") or "")
            _write_users(users)
        else:
            user_id = ""
    _revoke_user_runtime(user_id)
    return True


def set_preset(
    username: str, preset: AccountPreset, *, expected_user_id: str | None = None
) -> bool:
    """Update an account's configuration preset without changing its role."""
    if preset not in {"standard", "learner", "custom"}:
        raise ValueError("preset must be 'standard', 'learner', or 'custom'")
    with auth_store_transaction():
        users = load_users()
        if username not in users:
            return False
        if (
            expected_user_id is not None
            and str(users[username].get("id") or "") != expected_user_id
        ):
            return False
        users[username]["preset"] = preset
        _write_users(users)
    return True


def load_or_create_auth_secret() -> str:
    migrate_legacy_multi_user_tree()
    _migrate_secret()
    try:
        if SECRET_FILE.exists():
            existing = SECRET_FILE.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        generated = secrets.token_hex(32)
        write_secret_text(SECRET_FILE, generated)
        logger.warning(
            "Auth is enabled and no auth_secret file exists. Generated a stable local secret at %s.",
            SECRET_FILE,
        )
        return generated
    except Exception as exc:
        logger.warning("Failed to load/create auth secret at %s: %s", SECRET_FILE, exc)
        return secrets.token_hex(32)
