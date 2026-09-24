from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from deeptutor.partners.bus.queue import MessageBus
from deeptutor.partners.channels.whatsapp import WhatsAppChannel, WhatsAppConfig


def _channel() -> WhatsAppChannel:
    return WhatsAppChannel(
        WhatsAppConfig(enabled=True, allow_from=["*"]),
        MagicMock(spec=MessageBus),
    )


@pytest.mark.asyncio
async def test_bridge_qr_is_published_for_the_webui() -> None:
    channel = _channel()

    await channel._handle_bridge_message(json.dumps({"type": "qr", "qr": "scan-me"}))

    assert channel.setup_state == {
        "status": "waiting_for_scan",
        "qr_payload": "scan-me",
    }


@pytest.mark.asyncio
async def test_bridge_connection_status_is_published_for_the_webui() -> None:
    channel = _channel()

    await channel._handle_bridge_message(json.dumps({"type": "status", "status": "connected"}))

    assert channel.setup_state == {"status": "connected"}


@pytest.mark.asyncio
async def test_bridge_handshake_without_status_frame_is_not_left_connecting(
    monkeypatch,
) -> None:
    import websockets

    class _Socket:
        def __init__(self) -> None:
            self.closed = asyncio.Event()
            self.listening = asyncio.Event()

        async def send(self, _payload: str) -> None:
            return

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.listening.set()
            await self.closed.wait()
            raise StopAsyncIteration

        async def close(self) -> None:
            self.closed.set()

    socket = _Socket()

    class _Connection:
        async def __aenter__(self):
            return socket

        async def __aexit__(self, *_exc):
            await socket.close()

    monkeypatch.setattr(websockets, "connect", lambda _url: _Connection())
    channel = _channel()
    task = asyncio.create_task(channel.start())
    try:
        await asyncio.wait_for(socket.listening.wait(), timeout=1)
        assert channel._ws is socket
        assert channel.setup_state == {"status": "running"}
        await channel._handle_bridge_message(json.dumps({"type": "qr", "qr": "scan-me"}))
        assert channel.setup_state["status"] == "waiting_for_scan"
        await channel._handle_bridge_message(json.dumps({"type": "status", "status": "connected"}))
        assert channel.setup_state["status"] == "connected"
        await socket.close()
        await asyncio.sleep(0)
        assert channel.setup_state["status"] == "disconnected"
        assert channel._ws is None
        assert channel._connected is False
    finally:
        await channel.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
