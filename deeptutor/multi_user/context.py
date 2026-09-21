"""Request-local current user context."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from .models import CurrentUser
from .paths import local_admin_user, scope_for_user

_current_user: ContextVar[CurrentUser | None] = ContextVar("deeptutor_current_user", default=None)
_request_active: ContextVar[bool] = ContextVar("deeptutor_request_active", default=False)


class MissingUserScope(PermissionError):
    """A request attempted private access before authentication installed its scope."""


def set_current_user(user: CurrentUser) -> Token[CurrentUser | None]:
    return _current_user.set(user)


def reset_current_user(token: Token[CurrentUser | None]) -> None:
    _current_user.reset(token)


def get_current_user() -> CurrentUser:
    user = _current_user.get()
    if user is not None:
        return user
    if _request_active.get():
        raise MissingUserScope("An authenticated user scope is required")
    # Non-request startup and local CLI compatibility. ASGI always clears this
    # context and marks requests before routing, including public endpoints.
    return local_admin_user()


def get_current_user_or_none() -> CurrentUser | None:
    return _current_user.get()


def request_scope_active() -> bool:
    """Whether the ASGI request boundary has installed fail-closed scope."""

    return _request_active.get()


def user_from_token_payload(payload: Any | None) -> CurrentUser:
    if payload is None:
        return local_admin_user()
    user_id = str(getattr(payload, "user_id", "") or "")
    username = str(getattr(payload, "username", "") or "local")
    role = str(getattr(payload, "role", "user") or "user")
    # Decode normally performs this lookup already.  Re-check here as a second
    # boundary for adapters/tests that pass a TokenPayload directly: a stale
    # role claim must never turn into an admin CurrentUser after an account
    # change, and disabled/deleted accounts must fail closed.
    if user_id:
        from .identity import get_user_by_id

        record = get_user_by_id(user_id)
        if record is not None:
            record_username, record_data = record
            if bool(record_data.get("disabled", False)):
                raise PermissionError("This account is disabled")
            username = record_username
            role = str(record_data.get("role") or "user")
        else:
            from .identity import deleted_identity_revoked

            if deleted_identity_revoked(username, user_id):
                raise PermissionError("This account is no longer available")
    if role not in {"admin", "user"}:
        role = "user"
    if not user_id:
        user_id = "local-admin" if role == "admin" and username == "local" else username
    return CurrentUser(
        id=user_id,
        username=username,
        role=role,  # type: ignore[arg-type]
        scope=scope_for_user(user_id, is_admin=role == "admin"),
    )
