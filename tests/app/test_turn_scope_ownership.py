"""Turn commands must authorize the store before touching coordination."""

from __future__ import annotations

from pathlib import Path

import pytest

from deeptutor.app.service import TurnApplicationService
from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope
from deeptutor.services.session.scope import StoreScope


class _ForeignStore:
    store_scope = StoreScope(backend="sqlite", resource="foreign", owner_id="u_bob")

    async def get_turn(self, _turn_id: str):
        raise AssertionError("foreign store must not be read")


class _Provider:
    def __init__(self, store):
        self.store = store

    def get(self):
        return self.store


class _Coordinator:
    def __init__(self):
        self.calls = 0

    async def get_lease(self, _turn_id):
        self.calls += 1
        return None


@pytest.mark.asyncio
async def test_foreign_store_is_rejected_before_turn_lookup_or_coordinator() -> None:
    user = CurrentUser(
        id="u_alice",
        username="alice",
        role="user",
        scope=UserScope(kind="user", user_id="u_alice", root=Path("/tmp/alice")),
    )
    token = set_current_user(user)
    coordinator = _Coordinator()
    service = TurnApplicationService(_Provider(_ForeignStore()), object(), coordinator)
    try:
        with pytest.raises(PermissionError):
            await service.cancel_turn("turn_bob")
    finally:
        reset_current_user(token)
    assert coordinator.calls == 0
