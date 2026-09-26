"""QQ outbound sends must report SDK failures to the channel manager."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deeptutor.partners.bus.events import OutboundMessage
from deeptutor.partners.bus.queue import MessageBus
from deeptutor.partners.channels import qq as qq_mod
from deeptutor.partners.channels.qq import QQChannel, QQConfig


@pytest.mark.asyncio
@pytest.mark.parametrize("is_group", [False, True])
async def test_missing_sdk_response_raises_and_retry_keeps_message_sequence(is_group):
    group_send = AsyncMock(side_effect=[None, {"id": "sent"}, {"id": "next"}])
    c2c_send = AsyncMock(side_effect=[None, {"id": "sent"}, {"id": "next"}])
    channel = QQChannel(QQConfig(), MessageBus())
    channel._client = SimpleNamespace(
        api=SimpleNamespace(post_group_message=group_send, post_c2c_message=c2c_send)
    )
    chat_id = "group-openid" if is_group else "user-openid"
    if is_group:
        channel._chat_type_cache[chat_id] = "group"
    message = OutboundMessage(
        channel="qq",
        chat_id=chat_id,
        content="answer",
        metadata={"message_id": "incoming-id"},
    )

    with pytest.raises(RuntimeError, match="no response"):
        await channel.send(message)
    await channel.send(message)

    send = group_send if is_group else c2c_send
    other_send = c2c_send if is_group else group_send
    assert send.await_count == 2
    other_send.assert_not_awaited()
    first = send.await_args_list[0].kwargs
    retry = send.await_args_list[1].kwargs
    assert first == retry
    assert first["msg_id"] == "incoming-id"
    assert first["content"] == "answer"
    assert first["msg_seq"] == 2
    assert first["group_openid" if is_group else "openid"] == chat_id

    # A separate reply to the same incoming message gets a new sequence.
    await channel.send(
        OutboundMessage(
            channel="qq",
            chat_id=chat_id,
            content="follow-up",
            metadata={"message_id": "incoming-id"},
        )
    )
    assert send.await_args.kwargs["msg_seq"] == 3


@pytest.mark.asyncio
async def test_send_without_client_raises_for_manager_retry():
    channel = QQChannel(QQConfig(), MessageBus())
    message = OutboundMessage(channel="qq", chat_id="user-openid", content="answer")

    with pytest.raises(RuntimeError, match="not initialized"):
        await channel.send(message)


@pytest.mark.skipif(not qq_mod.QQ_AVAILABLE, reason="qq-botpy is not installed")
@pytest.mark.asyncio
async def test_bot_uses_longer_http_timeout():
    channel = QQChannel(QQConfig(), MessageBus())
    bot = qq_mod._make_bot_class(channel)()

    assert bot.http.timeout == 15
