"""Private, versioned model messages retained independently of display text.

Each assistant row owns one complete turn, including its prepared user input,
context updates and tool exchanges. Retention operates on these whole turns;
an older database without this metadata continues to use display history.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import logging
from typing import Any

MODEL_TURN_KEY = "model_turn"
MODEL_TURN_VERSION = 1
logger = logging.getLogger(__name__)
_MESSAGE_KEYS = frozenset(
    {
        "role",
        "content",
        "tool_calls",
        "tool_call_id",
        "name",
        "reasoning_content",
        "thinking_blocks",
        "_provider_response_state",
        "_context_snapshot",
    }
)


def _inline_image(part: Any) -> tuple[str, str] | None:
    """Read either provider's inline image without treating it as text."""
    if not isinstance(part, dict):
        return None
    if part.get("type") == "image_url":
        image_url = part.get("image_url") or {}
        url = image_url.get("url", "") if isinstance(image_url, dict) else image_url
        if isinstance(url, str) and url.startswith("data:image/") and ";base64," in url:
            header, encoded = url.split(";base64,", 1)
            return encoded, header[5:]
    elif part.get("type") == "image":
        source = part.get("source") or {}
        if isinstance(source, dict) and source.get("type") == "base64":
            encoded = source.get("data")
            if isinstance(encoded, str):
                return encoded, str(source.get("media_type") or "image/png")
    return None


def _image_fingerprint(encoded: str) -> str:
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def attachments_missing_from_history(
    messages: list[dict[str, Any]], attachments: list[Any]
) -> list[Any]:
    """Do not append historical images again after their original user turn.

    Call after hydrating history so legacy inline images and current local
    references compare using the same normalized model bytes. Newly uploaded
    images with inline bytes remain explicit inputs, even if the user sends
    the same picture again.
    """
    from deeptutor.services.llm.multimodal import resolve_image_for_model

    seen = {
        _image_fingerprint(image[0])
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if (image := _inline_image(part)) is not None
    }
    if not seen:
        return attachments
    selected = []
    for attachment in attachments:
        if getattr(attachment, "type", "") != "image" or getattr(attachment, "base64", ""):
            selected.append(attachment)
            continue
        try:
            resolved = resolve_image_for_model(url=getattr(attachment, "url", ""))
        except ValueError:
            resolved = None
        if resolved is None or _image_fingerprint(resolved[0]) not in seen:
            selected.append(attachment)
    return selected


async def archive_model_images(
    messages: list[dict[str, Any]], *, session_id: str, attachments: list[Any]
) -> list[dict[str, Any]]:
    """Persist attachment references, never inline image bytes, in model turns.

    Web inputs already have a scoped AttachmentStore URL. SDK/partner inputs
    can arrive without one; persist those using the same scoped store before
    retaining their model history. Resolution stays at the provider seam.
    """
    result = deepcopy(messages)
    if not any(
        _inline_image(part)
        for message in result
        if isinstance(message.get("content"), list)
        for part in message["content"]
    ):
        return result

    from deeptutor.services.llm.multimodal import resolve_image_for_model
    from deeptutor.services.storage import get_attachment_store

    references: dict[str, str] = {}
    for attachment in attachments:
        url = getattr(attachment, "url", "") or ""
        if getattr(attachment, "type", "") != "image" or not url.startswith("/files/attachments/"):
            continue
        try:
            resolved = resolve_image_for_model(url=url)
        except ValueError:
            continue
        if resolved is not None:
            references[_image_fingerprint(resolved[0])] = url

    for message in result:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for index, part in enumerate(content):
            image = _inline_image(part)
            if image is None:
                continue
            encoded, mime = image
            fingerprint = _image_fingerprint(encoded)
            try:
                url = references.get(fingerprint)
                if url is None:
                    extension = {"image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(
                        mime, "png"
                    )
                    url = await get_attachment_store().put(
                        session_id=session_id,
                        attachment_id=f"model-{fingerprint[:24]}",
                        filename=f"image.{extension}",
                        data=base64.b64decode(encoded, validate=True),
                        mime_type=mime,
                    )
                    references[fingerprint] = url
                image_url = {"url": url}
                if isinstance(part.get("image_url"), dict) and "detail" in part["image_url"]:
                    image_url["detail"] = part["image_url"]["detail"]
                content[index] = {"type": "image_url", "image_url": image_url}
            except Exception:
                # Do not let a failed optional history write inflate the DB
                # with base64 or discard the completed answer. A persisted
                # user attachment can still be re-injected on the next turn.
                logger.warning("Could not retain model image reference", exc_info=True)
                content[index] = {
                    "type": "text",
                    "text": "[Image unavailable in stored model history.]",
                }
    return result


def model_messages_token_count(messages: list[dict[str, Any]]) -> int:
    """Budget image blocks as images, not as millions of base64 text tokens."""
    from .context_builder import count_tokens

    image_count = 0
    cleaned = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"image_url", "image"}:
                    image_count += 1
                else:
                    parts.append(part)
            message = {**message, "content": parts}
        cleaned.append(message)
    # Provider image accounting differs; reserve a bounded planning estimate
    # per normalized image while retaining all text, tools and reasoning.
    return count_tokens(json.dumps(cleaned, ensure_ascii=False)) + image_count * 1024


def normalize_model_turn(value: Any) -> dict[str, Any] | None:
    """Read an internal turn record only when its tool protocol is complete.

    Unknown versions and incomplete writes fall back to the displayed exchange.
    Do not truncate reasoning, image blocks or tool arguments during replay.
    """
    if not isinstance(value, dict) or value.get("version") != MODEL_TURN_VERSION:
        return None
    if "system" in value and not isinstance(value["system"], str):
        return None
    tools = value.get("tools")
    if tools is not None:
        if not isinstance(tools, list):
            return None
        for tool in tools:
            if (
                not isinstance(tool, dict)
                or not isinstance(tool.get("function"), dict)
                or not isinstance(tool["function"].get("name"), str)
            ):
                return None
    route = value.get("route")
    if route is not None and (
        not isinstance(route, dict)
        or any(not isinstance(route.get(key), str) for key in ("provider", "model"))
    ):
        return None
    messages = value.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    pending: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            return None
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending:
                return None
            pending.remove(call_id)
        elif pending:
            return None
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list) or (calls and role != "assistant"):
            return None
        for call in calls:
            if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                return None
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id or call_id in pending:
                return None
            pending.add(call_id)
        if message.get("content") is not None and not isinstance(message["content"], (str, list)):
            return None
        cleaned.append({k: v for k, v in message.items() if k in _MESSAGE_KEYS})
    if pending:
        return None
    record = {"version": MODEL_TURN_VERSION, "messages": cleaned}
    for key in ("system", "tools", "route", "request_fingerprint"):
        if key in value:
            record[key] = value[key]
    try:
        return json.loads(json.dumps(record, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        return None


def model_turn(row: dict[str, Any]) -> dict[str, Any] | None:
    metadata = row.get("metadata")
    return (
        normalize_model_turn(metadata.get(MODEL_TURN_KEY)) if isinstance(metadata, dict) else None
    )


def history_groups(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Keep an admitted user and its assistant together across budget cuts."""
    groups: list[list[dict[str, Any]]] = []
    for row in rows:
        if (
            row.get("role") == "assistant"
            and model_turn(row) is not None
            and groups
            and groups[-1][-1].get("role") == "user"
        ):
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups


def replay_group(
    rows: list[dict[str, Any]], route: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Prefer the model's complete exchange over the corresponding UI rows."""
    from .context_builder import expand_message_context

    if rows and rows[-1].get("role") == "assistant":
        record = model_turn(rows[-1])
        if record is not None:
            messages = record["messages"]
            if route and record.get("route") and record["route"] != route:
                # Signed thinking/encrypted response items belong to the route
                # that produced them. Tool evidence and public text are portable.
                for message in messages:
                    for key in ("_provider_response_state", "thinking_blocks", "reasoning_content"):
                        message.pop(key, None)
                    for call in message.get("tool_calls") or []:
                        call.pop("extra_content", None)
            return messages
    messages = []
    for row in rows:
        expanded = expand_message_context(row)
        metadata = row.get("metadata") or {}
        if row.get("role") == "assistant" and expanded:
            from .provider_response_state import normalize_provider_response_state

            state = normalize_provider_response_state(metadata.get("provider_response_state"))
            if state:
                expanded[-1]["_provider_response_state"] = state
        messages.extend(expanded)
    return messages


def replay_history(
    rows: list[dict[str, Any]],
    summary: str = "",
    route: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    messages = (
        [{"role": "system", "content": f"[Conversation summary]\n{summary}"}] if summary else []
    )
    for group in history_groups(rows):
        messages.extend(replay_group(group, route))
    return messages


def complete_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Close calls interrupted before their results could be admitted."""
    result = deepcopy(messages)
    pending: dict[str, None] = {}
    for message in result:
        for call in message.get("tool_calls") or []:
            pending[call["id"]] = None
        if message.get("role") == "tool":
            pending.pop(message.get("tool_call_id"), None)
    for call_id in pending:
        result.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": "The turn was interrupted before this tool result was available. Do not assume it succeeded.",
            }
        )
    return result
