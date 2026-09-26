"""QQ channel implementation using botpy SDK."""

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urljoin
import uuid

import httpx
from loguru import logger
from pydantic import Field

from deeptutor.partners.bus.events import OutboundMessage
from deeptutor.partners.bus.queue import MessageBus
from deeptutor.partners.channels.base import BaseChannel
from deeptutor.partners.config.schema import DeliveryOverrides
from deeptutor.partners.helpers import detect_image_mime
from deeptutor.partners.network import validate_url_target

_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _attachment_field(attachment: Any, name: str) -> Any:
    if isinstance(attachment, dict):
        return attachment.get(name)
    return getattr(attachment, name, None)


def _is_image_attachment(attachment: Any) -> bool:
    content_type = str(_attachment_field(attachment, "content_type") or "").lower()
    return (
        not content_type
        or content_type == "application/octet-stream"
        or content_type.startswith("image/")
    )


def _group_element_images(elements: Any) -> tuple[list[dict[str, Any]], int]:
    """Find image attachments in QQ's nested group message elements.

    A group reply can contain the quoted image in ``msg_elements`` instead of
    the event's top-level ``attachments``. botpy 1.2.1 discards these elements
    while constructing GroupMessage, so this must run on the raw event.
    """
    images: list[dict[str, Any]] = []
    attachment_count = 0
    pending = [(elements, 0)]
    visited = 0
    while pending and visited < 64 and len(images) < 16:
        current, depth = pending.pop()
        if not isinstance(current, list):
            continue
        for element in current:
            if visited >= 64 or len(images) >= 16:
                break
            visited += 1
            if not isinstance(element, dict):
                continue
            attachments = element.get("attachments")
            if isinstance(attachments, list):
                for attachment in attachments:
                    if not isinstance(attachment, dict):
                        continue
                    attachment_count += 1
                    if _is_image_attachment(attachment):
                        images.append(attachment)
                        if len(images) >= 16:
                            break
            if depth < 4:
                pending.append((element.get("msg_elements"), depth + 1))
    return images, attachment_count


def _group_payload_with_images(payload: Any) -> Any:
    """Copy a raw group event with quoted images added to its attachments."""
    if not isinstance(payload, dict) or not isinstance(payload.get("d"), dict):
        return payload
    data = payload["d"]
    direct = data.get("attachments")
    direct_attachments = direct if isinstance(direct, list) else []
    nested_images, nested_count = _group_element_images(data.get("msg_elements"))
    seen_urls = {
        url
        for attachment in direct_attachments
        if isinstance(url := _attachment_field(attachment, "url"), str) and url
    }
    added: list[dict[str, Any]] = []
    for attachment in nested_images:
        url = attachment.get("url")
        if isinstance(url, str) and url:
            if url in seen_urls:
                continue
            seen_urls.add(url)
        added.append(attachment)

    logger.info(
        "QQ group event media counts: top_level={}, nested={}, image_candidates={}",
        len(direct_attachments),
        nested_count,
        sum(_is_image_attachment(item) for item in direct_attachments) + len(added),
    )
    if not added:
        return payload
    return {**payload, "d": {**data, "attachments": [*direct_attachments, *added]}}


try:
    import botpy
    from botpy.message import C2CMessage, GroupMessage

    QQ_AVAILABLE = True
except ImportError:
    QQ_AVAILABLE = False
    botpy = None
    C2CMessage = None
    GroupMessage = None

if TYPE_CHECKING:
    from botpy.message import C2CMessage, GroupMessage


def _make_bot_class(channel: "QQChannel") -> "type[botpy.Client]":
    """Create a botpy Client subclass bound to the given channel."""
    intents = botpy.Intents(public_messages=True, direct_message=True)

    class _Bot(botpy.Client):
        def __init__(self):
            # Disable botpy's file log — tutorbot uses loguru; default "botpy.log" fails on read-only fs
            super().__init__(intents=intents, ext_handlers=False)

        async def _bot_login(self, token):
            await super()._bot_login(token)
            # Install on this client only. botpy's GroupMessage omits msg_elements,
            # where QQ includes image attachments from a quoted group message.
            parser = self._connection.parser
            original = parser["group_at_message_create"]
            parser["group_at_message_create"] = lambda payload: original(
                _group_payload_with_images(payload)
            )

        async def on_ready(self):
            logger.info("QQ bot ready: {}", self.robot.name)
            channel.set_setup_state("connected")

        async def on_c2c_message_create(self, message: "C2CMessage"):
            await channel._on_message(message, is_group=False)

        async def on_group_at_message_create(self, message: "GroupMessage"):
            await channel._on_message(message, is_group=True)

        async def on_direct_message_create(self, message):
            await channel._on_message(message, is_group=False)

    return _Bot


class QQConfig(DeliveryOverrides):
    """QQ channel configuration using botpy SDK."""

    enabled: bool = False
    app_id: str = ""
    secret: str = ""
    allow_from: list[str] = Field(default_factory=list)
    msg_format: Literal["plain", "markdown"] = "plain"


class QQChannel(BaseChannel):
    """QQ channel using botpy SDK with WebSocket connection."""

    name = "qq"
    display_name = "QQ"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return QQConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = QQConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: QQConfig = config
        self._client: "botpy.Client | None" = None
        self._processed_ids: deque = deque(maxlen=1000)
        self._msg_seq: int = 1  # 消息序列号，避免被 QQ API 去重
        self._chat_type_cache: dict[str, str] = {}

    async def start(self) -> None:
        """Start the QQ bot."""
        if not QQ_AVAILABLE:
            logger.error("QQ SDK not installed. Run: pip install qq-botpy")
            self.set_setup_state(
                "unavailable",
                message="Required channel dependency is not installed on this server.",
            )
            return

        if not self.config.app_id or not self.config.secret:
            logger.error("QQ app_id and secret not configured")
            self.set_setup_state(
                "action_required",
                message=(
                    "Required fields are missing. Complete the channel configuration "
                    "and save again."
                ),
            )
            return

        self._running = True
        BotClass = _make_bot_class(self)
        self._client = BotClass()
        logger.info("QQ bot started (C2C & Group supported)")
        await self._run_bot()

    async def _run_bot(self) -> None:
        """Run the bot connection with auto-reconnect."""
        client = self._client
        if client is None:
            return
        while self._running:
            try:
                self.set_setup_state("connecting")
                await client.start(appid=self.config.app_id, secret=self.config.secret)
            except Exception as e:
                logger.warning("QQ bot error: {}", e)
                self.set_setup_state(
                    "error",
                    message="Channel connection failed; the listener will retry.",
                )
            if self._running:
                logger.info("Reconnecting QQ bot in 5 seconds...")
                await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the QQ bot."""
        self._running = False
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass
        logger.info("QQ bot stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through QQ.

        Raises on delivery failure so the channel manager's retry applies.
        """
        if not self._client:
            logger.warning("QQ client not initialized")
            return

        msg_id = msg.metadata.get("message_id")
        self._msg_seq += 1
        use_markdown = self.config.msg_format == "markdown"
        payload: dict[str, Any] = {
            "msg_type": 2 if use_markdown else 0,
            "msg_id": msg_id,
            "msg_seq": self._msg_seq,
        }
        if use_markdown:
            payload["markdown"] = {"content": msg.content}
        else:
            payload["content"] = msg.content

        chat_type = self._chat_type_cache.get(msg.chat_id, "c2c")
        if chat_type == "group":
            await self._client.api.post_group_message(
                group_openid=msg.chat_id,
                **payload,
            )
        else:
            await self._client.api.post_c2c_message(
                openid=msg.chat_id,
                **payload,
            )

    async def _on_message(self, data: "C2CMessage | GroupMessage", is_group: bool = False) -> None:
        """Handle incoming message from QQ."""
        try:
            # Dedup by message ID
            if data.id in self._processed_ids:
                return
            self._processed_ids.append(data.id)

            content = (data.content or "").strip()
            attachments = getattr(data, "attachments", None) or []
            if not content and not attachments:
                return

            if is_group:
                chat_id = data.group_openid
                user_id = data.author.member_openid
                self._chat_type_cache[chat_id] = "group"
            else:
                chat_id = str(
                    getattr(data.author, "id", None)
                    or getattr(data.author, "user_openid", "unknown")
                )
                user_id = chat_id
                self._chat_type_cache[chat_id] = "c2c"

            # Do not fetch media from a sender that the Partner will reject.
            if not self.is_allowed(user_id):
                await self._handle_message(
                    sender_id=user_id,
                    chat_id=chat_id,
                    content=content,
                    metadata={"message_id": data.id},
                )
                return

            image_attachments = [item for item in attachments if _is_image_attachment(item)]
            media_paths: list[str] = []
            for attachment in image_attachments:
                if path := await self._download_attachment(attachment):
                    media_paths.append(path)
            if len(media_paths) < len(image_attachments):
                failure_text = (
                    "部分图片未能读取，已收到的图片会继续处理。请重新发送失败的图片。"
                    if media_paths
                    else "图片未能读取，请重新发送不超过 8 MiB 的 JPG、PNG、GIF 或 WebP 图片。"
                )
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=self.name,
                        chat_id=chat_id,
                        content=failure_text,
                        metadata={"message_id": data.id},
                    )
                )
                if not media_paths:
                    return
            if not content and not media_paths:
                return

            await self._handle_message(
                sender_id=user_id,
                chat_id=chat_id,
                content=content,
                media=media_paths,
                metadata={"message_id": data.id},
            )
        except Exception:
            logger.exception("Error handling QQ message")

    async def _download_attachment(self, attachment: Any) -> str | None:
        """Download a QQ image to the Partner's media directory for the runner."""
        if not _is_image_attachment(attachment):
            return None

        url = _attachment_field(attachment, "url")
        if not isinstance(url, str) or not url:
            return None
        if url.startswith("//"):
            url = f"https:{url}"

        try:
            declared_size = int(_attachment_field(attachment, "size") or 0)
        except (TypeError, ValueError):
            declared_size = 0
        if declared_size > _MAX_IMAGE_BYTES:
            logger.warning("QQ image exceeds the 8 MiB limit")
            return None

        safe, reason = validate_url_target(url)
        if not safe:
            logger.warning("QQ image URL rejected: {}", reason)
            return None

        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=False, trust_env=False
            ) as client:
                target = url
                for _ in range(4):
                    async with client.stream("GET", target) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                logger.warning("QQ image redirect has no location")
                                return None
                            target = urljoin(target, location)
                            safe, reason = validate_url_target(target)
                            if not safe:
                                logger.warning("QQ image redirect rejected: {}", reason)
                                return None
                            continue
                        if response.status_code >= 300:
                            logger.warning(
                                "QQ image download returned HTTP {}", response.status_code
                            )
                            return None
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > _MAX_IMAGE_BYTES:
                                logger.warning("QQ image exceeds the 8 MiB limit")
                                return None
                        break
                else:
                    logger.warning("QQ image redirected too many times")
                    return None
        except httpx.HTTPError as exc:
            logger.warning("QQ image download failed: {}", type(exc).__name__)
            return None

        mime = detect_image_mime(data)
        if not mime:
            logger.warning("QQ attachment is not a supported image")
            return None
        try:
            path = self.media_dir() / f"{uuid.uuid4().hex}{_IMAGE_EXTENSIONS[mime]}"
            await asyncio.to_thread(path.write_bytes, data)
        except OSError:
            logger.warning("QQ image could not be saved")
            return None
        return str(path)
