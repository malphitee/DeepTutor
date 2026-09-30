"""Teacher and student accounts keep the same private isolation as ``user``."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

NON_ADMIN_ROLES = ["teacher", "student"]


@pytest.fixture
def local_auth(mu_isolated_root, monkeypatch):
    from deeptutor.services import auth

    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth, "POCKETBASE_ENABLED", False)
    return auth


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_stored_role_authenticates_into_a_private_user_scope(local_auth, role: str) -> None:
    from deeptutor.multi_user import identity
    from deeptutor.multi_user.context import user_from_token_payload

    identity.save_user("root", local_auth.hash_password("password1234"), role="admin")
    record = identity.save_user("carol", local_auth.hash_password("password1234"), role=role)

    payload = local_auth.decode_token(
        local_auth.create_token("carol", role=role, user_id=record["id"])
    )
    assert payload is not None
    assert payload.role == role

    current = user_from_token_payload(payload)
    assert current.role == role
    assert current.is_admin is False
    assert current.scope.kind == "user"
    assert current.scope.user_id == record["id"]
    assert current.scope.root.name == record["id"]
    assert current.scope.root.parent.name == "users"


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_changing_to_role_revokes_old_tokens(local_auth, role: str) -> None:
    from deeptutor.multi_user import identity

    identity.save_user("root", local_auth.hash_password("password1234"), role="admin")
    record = identity.save_user("carol", local_auth.hash_password("password1234"), role="user")
    old = local_auth.create_token("carol", role="user", user_id=record["id"])

    assert identity.set_role("carol", role) is True
    assert local_auth.decode_token(old) is None
    fresh = local_auth.decode_token(
        local_auth.create_token("carol", role=role, user_id=record["id"])
    )
    assert fresh is not None
    assert fresh.role == role


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_revocation_scans_the_accounts_private_turn_store(local_auth, role: str) -> None:
    from deeptutor.app.container import ApplicationContainer
    from deeptutor.multi_user import identity
    from deeptutor.multi_user.context import get_current_user

    identity.save_user("root", local_auth.hash_password("password1234"), role="admin")
    record = identity.save_user("carol", local_auth.hash_password("password1234"), role=role)
    scanned: list[tuple[str, str]] = []

    async def list_sessions(limit: int, offset: int) -> list[dict]:
        current = get_current_user()
        scanned.append((current.scope.kind, current.scope.user_id))
        return []

    container = ApplicationContainer.__new__(ApplicationContainer)
    container.store_provider = SimpleNamespace(
        get=lambda: SimpleNamespace(list_sessions=list_sessions)
    )
    container.runtime_registry = SimpleNamespace(get=lambda store: None)

    asyncio.run(container.revoke_user_turns(record["id"], previous_role=role))

    assert scanned == [("user", record["id"])]
