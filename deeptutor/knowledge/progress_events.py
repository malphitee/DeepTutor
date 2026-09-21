"""Application ports used by knowledge indexing to report live progress.

The knowledge domain owns progress facts but knows nothing about FastAPI,
WebSockets, or the API task-stream implementation.  API adapters install the
two callables for the lifetime of a server process; CLI/SDK use the no-op
defaults.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

# The scope keyword is intentionally part of the port.  A progress event is
# meaningful only inside the workspace that owns the KB.
ProgressBroadcast = Callable[..., Awaitable[None]]
TaskEventEmitter = Callable[[str, str, dict[str, Any]], None]

_broadcast: ProgressBroadcast | None = None
_emit_task_event: TaskEventEmitter | None = None


def install_progress_ports(
    *,
    broadcast: ProgressBroadcast | None,
    emit_task_event: TaskEventEmitter | None,
) -> None:
    global _broadcast, _emit_task_event
    _broadcast = broadcast
    _emit_task_event = emit_task_event


async def broadcast_progress(
    kb_name: str,
    progress: dict[str, Any],
    *,
    scope_key: str = "",
) -> None:
    if _broadcast is not None:
        try:
            await _broadcast(kb_name, progress, scope_key=scope_key)
        except TypeError:
            # Legacy CLI adapters may still expose the original two-argument
            # port.  Permit that only for an explicitly unscoped caller; a
            # tenant-scoped event must fail closed rather than being sent to a
            # global channel.
            if scope_key:
                raise
            await _broadcast(kb_name, progress)


def emit_task_progress(task_id: str, progress: dict[str, Any]) -> None:
    if _emit_task_event is not None:
        _emit_task_event(task_id, "progress", progress)


__all__ = [
    "broadcast_progress",
    "emit_task_progress",
    "install_progress_ports",
]
