"""Process-local revocation fan-out for live sockets and turn tasks.

JWT validation handles new requests.  Existing connections and asyncio tasks
need an explicit signal as soon as an administrator changes or removes an
account, so they cannot continue operating on the old scope until a token
expires.  The registry is deliberately process-local: each worker validates
the durable identity version and receives its own local admin mutation; the
coordinator still fences remote turns.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
import contextlib
from dataclasses import dataclass
import threading
from typing import Callable


@dataclass(frozen=True, slots=True)
class RevocationHandle:
    """One registered callback plus the loop it must be signalled on."""

    user_id: str
    loop: asyncio.AbstractEventLoop
    callback: Callable[[], None]


_lock = threading.Lock()
_handles: dict[str, set[RevocationHandle]] = defaultdict(set)


def register(user_id: str, callback: Callable[[], None]) -> RevocationHandle:
    """Subscribe *callback* to the next :func:`revoke` of *user_id*.

    The callback runs via ``call_soon_threadsafe`` on the registering event
    loop, so an admin mutation handled on another thread cancels sockets and
    turn tasks on their owning loop.
    """
    handle = RevocationHandle(str(user_id), asyncio.get_running_loop(), callback)
    with _lock:
        _handles[handle.user_id].add(handle)
    return handle


def unregister(handle: RevocationHandle | None) -> None:
    """Drop a handle; ``None`` (never registered / already cleaned) is a no-op."""
    if handle is None:
        return
    with _lock:
        handles = _handles.get(handle.user_id)
        if not handles:
            return
        handles.discard(handle)
        if not handles:
            _handles.pop(handle.user_id, None)


def revoke(user_id: str) -> int:
    """Signal all live local resources owned by *user_id*."""

    with _lock:
        handles = list(_handles.get(str(user_id), ()))
    for handle in handles:
        try:
            handle.loop.call_soon_threadsafe(handle.callback)
        except RuntimeError:
            unregister(handle)
    return len(handles)


def clear() -> None:
    """Test helper; production callers only add/remove individual handles."""

    with _lock:
        _handles.clear()


async def cleanup_websocket(scope: dict) -> None:
    """Release the watcher installed on an authenticated WebSocket.

    This lifecycle helper lives beside the revocation registry so the ASGI
    scope middleware does not need to import an API router (which would invert
    the multi-user/service dependency boundary).
    """

    handle, task = scope.pop("deeptutor.revocation", (None, None))
    unregister(handle)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


__all__ = [
    "RevocationHandle",
    "cleanup_websocket",
    "clear",
    "register",
    "revoke",
    "unregister",
]
