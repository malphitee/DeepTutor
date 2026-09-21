"""Authentication state changes invalidate already issued identities."""

from __future__ import annotations

import pytest


def test_role_change_rejects_old_token_and_uses_current_role(
    mu_isolated_root, monkeypatch
) -> None:
    from deeptutor.multi_user import identity
    from deeptutor.services import auth

    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth, "POCKETBASE_ENABLED", False)
    identity.save_user("root", auth.hash_password("password1234"), role="admin")
    record = identity.save_user("alice", auth.hash_password("password1234"), role="user")

    old = auth.create_token("alice", role="user", user_id=record["id"])
    assert auth.decode_token(old) is not None
    assert auth.decode_token(old).role == "user"

    assert identity.set_role("alice", "admin") is True
    assert auth.decode_token(old) is None

    fresh = auth.create_token("alice", role="user", user_id=record["id"])
    decoded = auth.decode_token(fresh)
    assert decoded is not None
    assert decoded.role == "admin"


@pytest.mark.parametrize("mutation", ["disable", "delete"])
def test_disabled_or_deleted_user_cannot_reuse_old_token(
    mu_isolated_root, monkeypatch, mutation: str
) -> None:
    from deeptutor.multi_user import identity
    from deeptutor.services import auth

    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth, "POCKETBASE_ENABLED", False)
    identity.save_user("root", auth.hash_password("password1234"), role="admin")
    record = identity.save_user("alice", auth.hash_password("password1234"), role="user")
    token = auth.create_token("alice", role="user", user_id=record["id"])

    if mutation == "disable":
        assert identity.set_disabled("alice", True) is True
    else:
        assert identity.delete_user("alice") is True

    assert auth.decode_token(token) is None


def test_revocation_fanout_signals_live_resources(mu_isolated_root) -> None:
    import asyncio

    from deeptutor.multi_user.revocation import register, revoke, unregister

    async def scenario() -> None:
        event = asyncio.Event()
        handle = register("u_alice", event.set)
        try:
            assert revoke("u_alice") == 1
            await asyncio.wait_for(event.wait(), timeout=1)
        finally:
            unregister(handle)

    asyncio.run(scenario())


def test_role_demotion_cancels_in_process_turn_tasks(mu_isolated_root) -> None:
    """Demoting an admin must stop their already-running turns in this worker.

    ``revoke_user_turns`` deliberately never scans the shared admin store
    (turn rows cannot be attributed to one account), so the in-process
    cancellation path — the revocation registry every turn task registers
    into — is what actually stops a demoted admin's live execution.
    """

    import asyncio

    from deeptutor.multi_user import identity
    from deeptutor.multi_user.revocation import register, unregister

    record = identity.save_user("root", identity.new_user_id(), role="admin")

    async def scenario() -> None:
        task = asyncio.create_task(asyncio.sleep(3600))
        handle = register(str(record["id"]), task.cancel)
        try:
            assert identity.set_role("root", "user") is True
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), timeout=1)
        finally:
            unregister(handle)

    asyncio.run(scenario())
