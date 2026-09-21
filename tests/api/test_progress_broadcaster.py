from __future__ import annotations

import pytest

from deeptutor.api.utils.progress_broadcaster import ProgressBroadcaster


class _Socket:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_json(self, frame: dict) -> None:
        self.frames.append(frame)


@pytest.mark.asyncio
async def test_same_named_kbs_do_not_share_progress_channel() -> None:
    broadcaster = ProgressBroadcaster()
    broadcaster._connections = {}
    alice = _Socket()
    bob = _Socket()

    await broadcaster.connect("notes", alice, scope_key="/data/users/u_alice/knowledge_bases")
    await broadcaster.connect("notes", bob, scope_key="/data/users/u_bob/knowledge_bases")

    await broadcaster.broadcast(
        "notes",
        {"percent": 50},
        scope_key="/data/users/u_alice/knowledge_bases",
    )

    assert alice.frames == [{"type": "progress", "data": {"percent": 50}}]
    assert bob.frames == []
