"""QQ SDK image attachments must reach the Partner message bus."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from deeptutor.partners import network as partner_network
from deeptutor.partners.bus.queue import MessageBus
from deeptutor.partners.channels import qq as qq_mod
from deeptutor.partners.channels.base import constructing_for
from deeptutor.partners.channels.qq import QQChannel, QQConfig
from deeptutor.partners.config import paths as partner_paths


_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/xusAAAAASUVORK5CYII="
)
_IMAGE_URL = "https://cdn.example.test/photo.png"
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _image_attachment() -> SimpleNamespace:
    # botpy.message.BaseMessage._Attachments exposes these as attributes.
    return SimpleNamespace(
        content_type="image/png",
        filename="photo.png",
        size=68,
        url="https://example.invalid/photo.png",
    )


def _message(*, content: str, is_group: bool = False) -> SimpleNamespace:
    author = (
        SimpleNamespace(member_openid="group-member")
        if is_group
        else SimpleNamespace(user_openid="c2c-user")
    )
    return SimpleNamespace(
        id="qq-image-message",
        content=content,
        author=author,
        group_openid="qq-group" if is_group else None,
        attachments=[_image_attachment()],
    )


def _local_image(tmp_path):
    image = tmp_path / "photo.png"
    image.write_bytes(_PNG_BYTES)
    return image


def _download_channel(tmp_path, monkeypatch):
    media_root = tmp_path / "partner_media"

    def partner_media_dir(partner_id: str, channel_name: str):
        assert (partner_id, channel_name) == ("test-partner", "qq")
        media_dir = media_root / partner_id / channel_name
        media_dir.mkdir(parents=True, exist_ok=True)
        return media_dir

    monkeypatch.setattr(partner_paths, "get_partner_media_dir", partner_media_dir)
    with constructing_for("test-partner"):
        channel = QQChannel(QQConfig(allow_from=["*"]), MessageBus())
    return channel, media_root / "test-partner" / "qq"


def _mock_image_response(monkeypatch, response_factory):
    """Route downloader traffic through httpx's in-memory transport."""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response_factory()

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        qq_mod.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(qq_mod, "validate_url_target", lambda url: (url == _IMAGE_URL, ""))
    return requests


@pytest.mark.asyncio
async def test_c2c_text_with_image_forwards_local_media(tmp_path, monkeypatch) -> None:
    bus = MessageBus()
    channel = QQChannel(QQConfig(allow_from=["*"]), bus)
    image = _local_image(tmp_path)
    download = AsyncMock(return_value=str(image))
    monkeypatch.setattr(channel, "_download_attachment", download, raising=False)
    attachment = _image_attachment()
    message = _message(content="What is in this picture?")
    message.attachments = [attachment]

    await channel._on_message(message)

    assert bus.inbound_size == 1
    inbound = bus.inbound.get_nowait()
    assert inbound.content == "What is in this picture?"
    assert inbound.media == [str(image)]
    assert inbound.metadata["message_id"] == message.id
    download.assert_awaited_once_with(attachment)


@pytest.mark.asyncio
async def test_c2c_image_without_text_is_forwarded(tmp_path, monkeypatch) -> None:
    bus = MessageBus()
    channel = QQChannel(QQConfig(allow_from=["*"]), bus)
    image = _local_image(tmp_path)
    monkeypatch.setattr(
        channel, "_download_attachment", AsyncMock(return_value=str(image)), raising=False
    )

    await channel._on_message(_message(content=""))

    assert bus.inbound_size == 1
    inbound = bus.inbound.get_nowait()
    assert inbound.content == ""
    assert inbound.media == [str(image)]


@pytest.mark.asyncio
async def test_group_image_is_forwarded_with_group_routing(tmp_path, monkeypatch) -> None:
    bus = MessageBus()
    channel = QQChannel(QQConfig(allow_from=["*"]), bus)
    image = _local_image(tmp_path)
    monkeypatch.setattr(
        channel, "_download_attachment", AsyncMock(return_value=str(image)), raising=False
    )

    await channel._on_message(_message(content="See this", is_group=True), is_group=True)

    assert bus.inbound_size == 1
    inbound = bus.inbound.get_nowait()
    assert inbound.sender_id == "group-member"
    assert inbound.chat_id == "qq-group"
    assert inbound.media == [str(image)]


@pytest.mark.asyncio
async def test_unavailable_image_reports_failure_instead_of_answering_without_it(monkeypatch) -> None:
    bus = MessageBus()
    channel = QQChannel(QQConfig(allow_from=["*"]), bus)
    monkeypatch.setattr(channel, "_download_attachment", AsyncMock(return_value=None))

    await channel._on_message(_message(content="What is in this image?"))

    assert bus.inbound_size == 0
    assert bus.outbound_size == 1
    reply = bus.outbound.get_nowait()
    assert reply.channel == "qq"
    assert reply.chat_id == "c2c-user"
    assert "图片" in reply.content
    assert reply.metadata["message_id"] == "qq-image-message"


@pytest.mark.asyncio
async def test_valid_png_downloads_to_partner_media_dir(tmp_path, monkeypatch) -> None:
    channel, media_dir = _download_channel(tmp_path, monkeypatch)
    requests = _mock_image_response(
        monkeypatch, lambda: httpx.Response(200, content=_PNG_BYTES)
    )
    attachment = _image_attachment()
    attachment.url = _IMAGE_URL

    local_path = await channel._download_attachment(attachment)

    assert local_path is not None
    assert Path(local_path).parent == media_dir
    assert local_path.endswith(".png")
    assert Path(local_path).read_bytes() == _PNG_BYTES
    assert len(requests) == 1
    assert str(requests[0].url) == _IMAGE_URL


@pytest.mark.asyncio
async def test_scheme_relative_qq_image_url_is_downloaded(tmp_path, monkeypatch) -> None:
    channel, _media_dir = _download_channel(tmp_path, monkeypatch)
    requests = _mock_image_response(
        monkeypatch, lambda: httpx.Response(200, content=_PNG_BYTES)
    )
    attachment = _image_attachment()
    attachment.url = "//cdn.example.test/photo.png"

    local_path = await channel._download_attachment(attachment)

    assert local_path is not None
    assert Path(local_path).read_bytes() == _PNG_BYTES
    assert [str(request.url) for request in requests] == [_IMAGE_URL]


@pytest.mark.asyncio
async def test_declared_oversized_image_is_rejected_before_fetch(tmp_path, monkeypatch) -> None:
    channel, media_dir = _download_channel(tmp_path, monkeypatch)
    requests = _mock_image_response(
        monkeypatch, lambda: httpx.Response(200, content=_PNG_BYTES)
    )
    attachment = _image_attachment()
    attachment.url = _IMAGE_URL
    attachment.size = _MAX_IMAGE_BYTES + 1

    assert await channel._download_attachment(attachment) is None
    assert requests == []
    assert not media_dir.exists()


class _OversizedImageStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield _PNG_BYTES + b"x" * (4 * 1024 * 1024)
        yield b"x" * (4 * 1024 * 1024)


@pytest.mark.asyncio
async def test_streamed_oversized_image_is_rejected(tmp_path, monkeypatch) -> None:
    channel, media_dir = _download_channel(tmp_path, monkeypatch)
    requests = _mock_image_response(
        monkeypatch, lambda: httpx.Response(200, stream=_OversizedImageStream())
    )
    attachment = _image_attachment()
    attachment.url = _IMAGE_URL

    assert await channel._download_attachment(attachment) is None
    assert len(requests) == 1
    assert not media_dir.exists()


@pytest.mark.asyncio
async def test_non_image_bytes_are_rejected(tmp_path, monkeypatch) -> None:
    channel, media_dir = _download_channel(tmp_path, monkeypatch)
    requests = _mock_image_response(
        monkeypatch, lambda: httpx.Response(200, content=b"not an image")
    )
    attachment = _image_attachment()
    attachment.url = _IMAGE_URL

    assert await channel._download_attachment(attachment) is None
    assert len(requests) == 1
    assert not media_dir.exists()


@pytest.mark.asyncio
async def test_redirected_image_is_downloaded_after_validating_destination(
    tmp_path, monkeypatch
) -> None:
    channel, _media_dir = _download_channel(tmp_path, monkeypatch)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"location": "/actual.png"})
        return httpx.Response(200, content=_PNG_BYTES)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        qq_mod.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    validated: list[str] = []

    def validate(url: str):
        validated.append(url)
        return True, ""

    monkeypatch.setattr(qq_mod, "validate_url_target", validate)
    attachment = _image_attachment()
    attachment.url = _IMAGE_URL

    local_path = await channel._download_attachment(attachment)

    assert local_path is not None
    assert Path(local_path).read_bytes() == _PNG_BYTES
    assert [str(request.url) for request in requests] == [
        _IMAGE_URL,
        "https://cdn.example.test/actual.png",
    ]
    assert validated == [str(request.url) for request in requests]


@pytest.mark.asyncio
async def test_private_redirect_is_not_fetched(tmp_path, monkeypatch) -> None:
    channel, media_dir = _download_channel(tmp_path, monkeypatch)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/image"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        qq_mod.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(
        qq_mod,
        "validate_url_target",
        lambda url: (url == _IMAGE_URL, "blocked private redirect"),
    )
    attachment = _image_attachment()
    attachment.url = _IMAGE_URL

    assert await channel._download_attachment(attachment) is None
    assert len(requests) == 1
    assert not media_dir.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_url", ["file:///etc/passwd", "http://127.0.0.1/image"])
async def test_unsafe_url_is_rejected_without_fetch(tmp_path, monkeypatch, unsafe_url) -> None:
    channel, media_dir = _download_channel(tmp_path, monkeypatch)
    monkeypatch.setattr(
        partner_network.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (partner_network.socket.AF_INET, partner_network.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))
        ],
    )
    monkeypatch.setattr(
        qq_mod.httpx, "AsyncClient", lambda **_kwargs: pytest.fail("network fetch attempted")
    )
    attachment = _image_attachment()
    attachment.url = unsafe_url

    assert await channel._download_attachment(attachment) is None
    assert not media_dir.exists()


@pytest.mark.asyncio
async def test_unauthorized_sender_does_not_download_image(monkeypatch) -> None:
    bus = MessageBus()
    channel = QQChannel(QQConfig(allow_from=["someone-else"]), bus)
    download = AsyncMock(return_value="/tmp/should-not-exist.png")
    monkeypatch.setattr(channel, "_download_attachment", download)

    await channel._on_message(_message(content=""))

    download.assert_not_awaited()
    assert bus.inbound_size == 0
