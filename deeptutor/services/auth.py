"""
Authentication service for DeepTutor.

Disabled by default (auth.enabled=false) so localhost users are unaffected.
When enabled, guards all API routes with JWT bearer tokens.

Quick setup (single user via data/user/settings/auth.json):
    1. Set enabled=true
    2. Set username=<your username>
    3. Generate a password hash:
           python -c "from deeptutor.services.auth import hash_password; print(hash_password('yourpassword'))"
       Paste the output into password_hash=<hash>

Multi-user setup (recommended):
    Enable auth and leave username/password_hash empty.
    Navigate to /register in the browser. The first user to register is granted
    admin privileges and can manage other users from /admin/users.

    Users are stored in data/system/auth/users.json:
        {
            "alice": {"hash": "$2b$12$...", "role": "admin", "created_at": "2026-..."},
            "bob":   {"hash": "$2b$12$...", "role": "user",  "created_at": "2026-..."}
        }
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from deeptutor.multi_user.models import AccountPreset, Role
from deeptutor.services.config import load_auth_settings, load_integrations_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — read once at import time from runtime JSON settings
# ---------------------------------------------------------------------------

_AUTH_SETTINGS = load_auth_settings()
_INTEGRATIONS_SETTINGS = load_integrations_settings()

AUTH_ENABLED: bool = bool(_AUTH_SETTINGS["enabled"])
AUTH_USERNAME: str = str(_AUTH_SETTINGS["username"])
AUTH_PASSWORD_HASH: str = str(_AUTH_SETTINGS["password_hash"])
AUTH_SECRET: str = ""
TOKEN_EXPIRE_HOURS: int = int(_AUTH_SETTINGS["token_expire_hours"])

# PocketBase auth mode — active when integrations.pocketbase_url is set and auth is enabled.
# When enabled, login/register proxy to PocketBase and token validation uses
# PocketBase's auth-refresh endpoint (cached in memory — no static secret needed).
POCKETBASE_BASE_URL: str = str(_INTEGRATIONS_SETTINGS["pocketbase_url"]).rstrip("/")
POCKETBASE_ENABLED: bool = bool(POCKETBASE_BASE_URL) and AUTH_ENABLED

_ALGORITHM = "HS256"


if AUTH_ENABLED and not POCKETBASE_ENABLED and not AUTH_SECRET:
    from deeptutor.multi_user.identity import load_or_create_auth_secret

    AUTH_SECRET = load_or_create_auth_secret()


# ---------------------------------------------------------------------------
# Token payload
# ---------------------------------------------------------------------------


@dataclass
class TokenPayload:
    """Decoded JWT payload."""

    username: str
    role: str
    user_id: str = ""
    device_credential_id: str = ""
    device_session_nonce: str = ""
    token_version: int = 0


def assert_supported_backend() -> None:
    """Reject the old PocketBase control plane in multi-user deployments.

    PocketBase stores sessions outside the per-user SQLite trees and has no
    equivalent for DeepTutor's grants, path scopes, or immediate revocation.
    It remains available only for the historical auth-disabled/single-user
    compatibility mode; enabling local authentication with it is an invalid
    deployment rather than a partially isolated one.
    """

    if AUTH_ENABLED and POCKETBASE_ENABLED:
        raise RuntimeError(
            "PocketBase cannot be combined with built-in multi-user authentication; "
            "unset integrations.pocketbase_url and use data/system plus data/users."
        )


def log_isolation_mode() -> None:
    """State the deployment's isolation posture at startup.

    A single-user compatibility deployment must be distinguishable from a
    multi-user isolated one in the startup log, so an operator cannot mistake
    a shared-workspace configuration for the isolated multi-user mode
    (user-isolation plan, Phase 6).  The compatibility posture logs at
    WARNING because the shipped default log level is WARNING and that is
    exactly the posture an operator must not mistake for isolation.
    """
    import os

    if not AUTH_ENABLED:
        logger.warning(
            "Isolation mode: single-user compatibility — authentication is disabled; "
            "every request runs as the local admin over the shared data/ workspace"
        )
        return
    from deeptutor.multi_user.paths import USERS_ROOT

    logger.info(
        "Isolation mode: multi-user isolated — per-user workspaces under %s with "
        "the built-in identity store",
        USERS_ROOT,
    )
    shared_root = str(os.environ.get("DEEPTUTOR_WORKSPACE_ROOT", "") or "").strip()
    if shared_root:
        logger.warning(
            "DEEPTUTOR_WORKSPACE_ROOT=%s is a deployment-wide shared root; with "
            "authentication enabled it stays admin-reachable and must not be used "
            "as a per-user workspace",
            shared_root,
        )


# ---------------------------------------------------------------------------
# Password hashing — uses bcrypt directly (passlib is unmaintained for bcrypt 4+)
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Hash a plaintext password. Use this to generate password hashes."""
    import bcrypt

    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    import bcrypt

    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def validate_new_password(value: str) -> str:
    """Shared password policy for registration and self-service changes."""
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 UTF-8 bytes")
    return value


class PasswordChangeError(ValueError):
    """A rejected self-service password change, with a stable API error code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def change_password(current: TokenPayload, current_password: str, new_password: str) -> None:
    """Verify the caller's password and atomically revoke its old authentication state.

    Run in a worker thread: bcrypt verification and hashing must not block the
    event loop or hold the identity-store lock while doing expensive work.
    """
    from deeptutor.multi_user.identity import get_user, set_password

    if not AUTH_ENABLED or POCKETBASE_ENABLED or current.user_id == "env-admin":
        raise PasswordChangeError("password_change_unsupported")
    validate_new_password(new_password)
    record = get_user(current.username)
    if (
        not record
        or record.get("id") != current.user_id
        or record.get("disabled", False)
        or record.get("role") != current.role
        or int(record.get("token_version", 0)) != current.token_version
    ):
        raise PasswordChangeError("session_expired")
    if len(current_password.encode("utf-8")) > 72 or not verify_password(
        current_password, record["hash"]
    ):
        raise PasswordChangeError("current_password_incorrect")
    updated = set_password(
        current.username,
        hash_password(new_password),
        expected_user_id=current.user_id,
        expected_token_version=current.token_version,
        expected_hash=record["hash"],
    )
    if updated is None:
        raise PasswordChangeError("session_expired")


# ---------------------------------------------------------------------------
# User store — multi-user JSON store plus optional auth.json bootstrap user
# ---------------------------------------------------------------------------


def _make_user_record(
    hashed: str,
    role: str = "user",
    created_at: str = "",
    preset: str = "standard",
) -> dict[str, Any]:
    """Build a canonical user record dict for legacy callers/tests."""
    from deeptutor.multi_user.identity import new_user_id

    return {
        "id": new_user_id(),
        "hash": hashed,
        "role": role,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "disabled": False,
        "avatar": "",
        "preset": preset,
    }


def _load_users() -> dict[str, dict]:
    """
    Load the user store, migrating old flat format if needed.

    Priority:
      1. multi-user identity store
      2. auth.json username + password_hash — single-user bootstrap user

    Old format: {"alice": "$2b$12$..."}
    New format: {"alice": {"hash": "...", "role": "admin", "created_at": "..."}}
    """
    from deeptutor.multi_user.identity import load_users

    return load_users(AUTH_USERNAME, AUTH_PASSWORD_HASH)


def is_first_user() -> bool:
    """Return True when no users exist yet (first registration will become admin)."""
    return len(_load_users()) == 0


def account_by_id(user_id: str) -> tuple[str, dict] | None:
    """Resolve ``(username, record)`` for an account id.

    Unlike :func:`deeptutor.multi_user.identity.get_user_by_id`, this includes
    the ``auth.json`` bootstrap admin, which exists only in the in-memory
    overlay that ``decode_token`` authorizes against — callers revalidating a
    decoded payload must see the same set of accounts.
    """
    if not user_id:
        return None
    for username, record in _load_users().items():
        if str(record.get("id") or "") == str(user_id):
            return username, record
    return None


def add_user(
    username: str,
    plain_password: str,
    role: Role = "user",
    preset: AccountPreset = "standard",
) -> None:
    """
    Add or update a user in data/user/auth_users.json.

    The role defaults to 'user'. Pass role='admin' to elevate. When the store
    is empty the first user is automatically promoted to 'admin' regardless of
    the role argument.

    Creates the file (and parent directories) if they don't exist.
    """
    from deeptutor.multi_user.identity import save_user

    record = save_user(
        username,
        hash_password(plain_password),
        role=role,
        preset=preset,
    )
    logger.info(
        "User '%s' saved with role=%r preset=%r",
        username,
        record.get("role", "user"),
        record.get("preset", "standard"),
    )


def list_users() -> list[dict]:
    """Return a list of user info dicts (username, role, created_at) — no hashes."""
    from deeptutor.multi_user.identity import list_user_info

    return list_user_info(AUTH_USERNAME, AUTH_PASSWORD_HASH)


def delete_user(username: str) -> bool:
    """
    Remove a user from the store. Returns True if the user existed.

    """
    from deeptutor.multi_user.identity import delete_user as _delete_user

    if not _delete_user(username):
        return False
    logger.info("User '%s' deleted", username)
    return True


def set_role(username: str, role: str) -> bool:
    """
    Change the role for an existing user. Returns True on success.

    Valid roles are the entries of ``VALID_ROLES`` (currently 'admin', 'user').
    """
    from deeptutor.multi_user.models import VALID_ROLES

    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role!r}. Must be one of {sorted(VALID_ROLES)}.")

    from deeptutor.multi_user.identity import set_role as _set_role

    if not _set_role(username, role):  # type: ignore[arg-type]
        return False
    logger.info(f"User '{username}' role updated to {role!r}")
    return True


def set_disabled(username: str, disabled: bool) -> bool:
    """Enable/disable a local account and revoke all previously issued JWTs."""

    from deeptutor.multi_user.identity import set_disabled as _set_disabled

    if not _set_disabled(username, disabled):
        return False
    logger.info("User '%s' disabled=%s", username, disabled)
    return True


def set_avatar(username: str, avatar: str) -> bool:
    """
    Update the avatar marker for an existing user. Returns True on success.

    The marker is either '' (deterministic fallback), 'icon:<name>:<color>',
    or 'img:<version>' (managed by the avatar upload endpoint).
    """
    from deeptutor.multi_user.identity import set_avatar as _set_avatar

    if not _set_avatar(username, avatar):
        return False
    logger.info("User '%s' avatar updated", username)
    return True


def get_user_info(username: str) -> dict | None:
    """Return the public info dict for a single user, or None if unknown."""
    for item in list_users():
        if item.get("username") == username:
            return item
    return None


def get_learner_profile(username: str) -> dict[str, Any] | None:
    """Return the structured learner profile for an existing account."""
    from deeptutor.multi_user.identity import get_learner_profile as _get_profile

    return _get_profile(username)


def set_learner_profile(username: str, profile: dict[str, Any] | None) -> dict[str, Any] | None:
    """Replace the structured learner profile for an existing account."""
    from deeptutor.multi_user.identity import set_learner_profile as _set_profile

    return _set_profile(username, profile)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


class StaleAuthenticationError(ValueError):
    """Credentials were revoked between authentication and token issuance."""


def create_token(
    username: str,
    role: str = "user",
    user_id: str | None = None,
    device_credential_id: str = "",
    device_session_nonce: str = "",
    *,
    expected_token_version: int | None = None,
) -> str:
    """Create a signed JWT for the given username and role."""
    from jose import jwt

    record = _load_users().get(username) or {}
    if expected_token_version is not None and (
        not record
        or record.get("disabled", False)
        or record.get("id") != user_id
        or int(record.get("token_version", 0)) != expected_token_version
    ):
        raise StaleAuthenticationError("Authentication state changed. Sign in again.")
    if record:
        # Never mint a token with a caller-supplied role for a known account.
        # The identity store is authoritative; the role claim is only a
        # cache for old clients and is revalidated by decode_token below.
        role = str(record.get("role") or "user")
        user_id = str(record.get("id") or user_id or "")
        token_version = max(0, int(record.get("token_version", 0) or 0))
    else:
        token_version = 0
        user_id = user_id or ""

    payload = {
        "sub": username,
        "role": role,
        "uid": user_id,
        "ver": token_version,
        "dcid": device_credential_id,
        "dcs": device_session_nonce,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, AUTH_SECRET, algorithm=_ALGORITHM)


def decode_token(token: str) -> TokenPayload | None:
    """
    Validate a token and return a TokenPayload, or None if invalid.

    - PocketBase mode: calls PocketBase's auth-refresh endpoint (cached in
      memory for 60 s, so only the first request per token per minute makes
      a network call). No static JWT secret required.
    - Standard mode: local in-memory jwt.decode() using AUTH_SECRET — zero
      network calls, same as before.
    """
    if not token:
        return None

    if POCKETBASE_ENABLED:
        assert_supported_backend()
        from deeptutor.services.pocketbase_client import validate_pb_token

        payload = validate_pb_token(token)
        if payload is None:
            return None
        return TokenPayload(
            username=payload["username"],
            role=payload.get("role", "user"),
            user_id=str(payload.get("id") or payload.get("uid") or payload.get("user_id") or ""),
        )

    # Standard JWT + bcrypt mode
    from jose import JWTError, jwt

    if not AUTH_SECRET:
        return None

    try:
        payload = jwt.decode(token, AUTH_SECRET, algorithms=[_ALGORITHM])
        username = payload.get("sub")
        if not username:
            return None
        claimed_user_id = str(payload.get("uid") or "")
        record = _load_users().get(str(username)) or {}
        if not record:
            # A few integrations mint a signed non-admin token before their
            # compatibility account is materialized locally. Keep that narrow
            # compatibility path, while a durable delete tombstone still
            # invalidates an old token.
            from deeptutor.multi_user.identity import deleted_identity_revoked

            if deleted_identity_revoked(str(username), claimed_user_id):
                return None
            role = str(payload.get("role") or "user")
            if role != "user":
                return None
            user_id = claimed_user_id or str(username)
            current_version = 0
        else:
            if bool(record.get("disabled", False)):
                return None
            user_id = str(record.get("id") or "")
            if claimed_user_id and claimed_user_id != user_id:
                return None
            try:
                claimed_version = int(payload.get("ver", 0) or 0)
                current_version = int(record.get("token_version", 0) or 0)
            except (TypeError, ValueError):
                return None
            if claimed_version != current_version:
                return None
            role = str(record.get("role") or "user")
            if role not in {"admin", "user"}:
                return None
        device_credential_id = str(payload.get("dcid") or "")
        device_session_nonce = str(payload.get("dcs") or "")
        if device_credential_id:
            from deeptutor.multi_user.device_credentials import validate_device_token

            if not validate_device_token(
                user_id,
                device_credential_id,
                device_session_nonce,
            ):
                return None
        return TokenPayload(
            username=username,
            role=role,
            user_id=user_id,
            device_credential_id=device_credential_id,
            device_session_nonce=device_session_nonce,
            token_version=current_version,
        )
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# PocketBase auth helpers
# ---------------------------------------------------------------------------


def authenticate_pb(username: str, password: str) -> tuple[TokenPayload, str] | None:
    """
    Authenticate against PocketBase and return (TokenPayload, raw_pb_token).

    Only called when POCKETBASE_ENABLED=True.
    Returns None on failure.
    The raw token is the PocketBase JWT string to be stored in the cookie.

    PocketBase requires an email address; plain usernames are mapped to
    <username>@deeptutor.local to match the email used at registration.
    """
    assert_supported_backend()
    try:
        from deeptutor.services.pocketbase_client import get_pb_client

        pb = get_pb_client()
        result = pb.collection("users").auth_with_password(username, password)
        token: str = result.token
        record = result.record
        username = (
            getattr(record, "email", None)
            or getattr(record, "name", None)
            or getattr(record, "id", "unknown")
        )
        # PocketBase has no built-in "role" field by default; treat all as "user".
        # Admins authenticated via PocketBase admin panel use a separate endpoint.
        role = getattr(record, "role", "user") or "user"
        user_id = str(getattr(record, "id", "") or "")
        return TokenPayload(username=str(username), role=str(role), user_id=user_id), token
    except Exception as exc:
        logger.warning(f"PocketBase authentication failed: {exc}")
        return None


def register_pb(username: str, email: str, password: str) -> dict | None:
    """
    Create a new user in PocketBase.

    Returns the created user record dict or None on failure.
    """
    assert_supported_backend()
    try:
        from deeptutor.services.pocketbase_client import get_pb_client

        pb = get_pb_client()
        record = pb.collection("users").create(
            {
                "username": username,
                "email": email,
                "password": password,
                "passwordConfirm": password,
            }
        )
        return {"id": record.id, "username": username, "email": email}
    except Exception as exc:
        logger.warning(f"PocketBase registration failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Main auth entry point
# ---------------------------------------------------------------------------


def authenticate(username: str, password: str) -> TokenPayload | None:
    """
    Validate credentials. Returns a TokenPayload on success, None on failure.

    When auth is disabled, always returns a dummy admin payload so that
    callers don't need to special-case the disabled state.
    """
    if not AUTH_ENABLED:
        return TokenPayload(username=username or "local", role="admin", user_id="local-admin")

    assert_supported_backend()

    users = _load_users()
    if not users:
        logger.warning(
            "No users configured — login will always fail. "
            "Navigate to /register to create your first account."
        )
        return None

    record = users.get(username)
    if not record:
        return None

    if isinstance(record, dict) and bool(record.get("disabled", False)):
        return None

    hashed = record.get("hash", "") if isinstance(record, dict) else record
    if not verify_password(password, hashed):
        return None

    role = record.get("role", "user") if isinstance(record, dict) else "user"
    user_id = str(record.get("id") or "") if isinstance(record, dict) else ""
    return TokenPayload(
        username=username,
        role=role,
        user_id=user_id,
        token_version=max(0, int(record.get("token_version", 0) or 0)),
    )


def authenticate_device(pairing_code: str, pin: str) -> TokenPayload | None:
    """Exchange a learner device credential for the account's normal JWT identity."""

    if not AUTH_ENABLED or POCKETBASE_ENABLED:
        return None
    from deeptutor.multi_user.device_credentials import begin_device_session

    session = begin_device_session(pairing_code, pin)
    if session is None:
        return None
    _view, username, role, user_id, session_nonce = session
    return TokenPayload(
        username=username,
        role=role,
        user_id=user_id,
        device_credential_id=str(_view["id"]),
        device_session_nonce=session_nonce,
    )
