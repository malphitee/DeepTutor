"""
Progress Broadcaster - Manages WebSocket broadcasting of knowledge base progress
"""

import asyncio
import logging
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ProgressBroadcaster:
    """Manages WebSocket broadcasting of knowledge base progress"""

    _instance: Optional["ProgressBroadcaster"] = None
    # Connections are partitioned by both the KB name and its resolved scope.
    # A name such as ``notes`` is valid in every user workspace, so using the
    # name alone would let progress from one tenant reach another tenant's
    # socket.
    _connections: dict[str, set[WebSocket]] = {}
    _lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "ProgressBroadcaster":
        """Get singleton instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _key(kb_name: str, scope_key: str = "") -> str:
        """Return the tenant-qualified broadcaster key.

        ``scope_key`` is normally the canonical knowledge-base root.  The
        empty value remains supported for CLI/tests and legacy callers; API
        WebSocket handlers always provide a resolved scope.
        """

        return f"{scope_key}\x1f{kb_name}" if scope_key else kb_name

    async def connect(self, kb_name: str, websocket: WebSocket, *, scope_key: str = ""):
        """Connect WebSocket to specified knowledge base"""
        key = self._key(kb_name, scope_key)
        async with self._lock:
            if key not in self._connections:
                self._connections[key] = set()
            self._connections[key].add(websocket)
            logger.debug(
                "Connected WebSocket for KB '%s' scope '%s' (total: %s)",
                kb_name,
                scope_key or "legacy",
                len(self._connections[key]),
            )

    async def disconnect(self, kb_name: str, websocket: WebSocket, *, scope_key: str = ""):
        """Disconnect WebSocket connection"""
        key = self._key(kb_name, scope_key)
        async with self._lock:
            if key in self._connections:
                self._connections[key].discard(websocket)
                if not self._connections[key]:
                    del self._connections[key]
                logger.debug("Disconnected WebSocket for KB '%s' scope '%s'", kb_name, scope_key)

    async def broadcast(self, kb_name: str, progress: dict, *, scope_key: str = ""):
        """Broadcast progress update to all WebSocket connections for specified knowledge base"""
        key = self._key(kb_name, scope_key)
        async with self._lock:
            if key not in self._connections:
                return

            # Create list of connections to remove (closed connections)
            to_remove = []

            for websocket in self._connections[key]:
                try:
                    await websocket.send_json({"type": "progress", "data": progress})
                except Exception as e:
                    # Connection closed or error, mark for removal
                    logger.debug("Error sending to WebSocket for KB '%s': %s", kb_name, e)
                    to_remove.append(websocket)

            # Remove closed connections
            for ws in to_remove:
                self._connections[key].discard(ws)

            if not self._connections[key]:
                del self._connections[key]

    def get_connection_count(self, kb_name: str, *, scope_key: str = "") -> int:
        """Get connection count for specified knowledge base"""
        return len(self._connections.get(self._key(kb_name, scope_key), set()))
