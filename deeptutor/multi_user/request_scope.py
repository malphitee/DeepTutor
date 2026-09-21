"""ASGI boundary: no request inherits a deployment or a previous user's scope."""

from __future__ import annotations

import logging

from starlette.types import ASGIApp, Receive, Scope, Send

from .context import _current_user, _request_active

logger = logging.getLogger(__name__)


class UserScopeMiddleware:
    """Mark every HTTP/WS request as fail-closed before routing begins.

    Clearing ``_current_user`` at the boundary means an unauthenticated
    handler raises ``MissingUserScope`` instead of silently falling back to
    the local admin identity; non-request code paths (startup, CLI, jobs)
    keep their admin-compatible default.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        active = _request_active.set(True)
        user = _current_user.set(None)
        try:
            await self.app(scope, receive, send)
        finally:
            if scope["type"] == "websocket":
                # ``ws_require_auth`` stores its process-local revocation
                # watcher on the mutable ASGI scope.  Clean it up centrally so
                # every WebSocket endpoint gets the same lifecycle even when a
                # handler only resets its ContextVar token.
                try:
                    from .revocation import cleanup_websocket

                    await cleanup_websocket(scope)
                except Exception:
                    logger.exception("Failed to clean up WebSocket revocation watcher")
            _current_user.reset(user)
            _request_active.reset(active)
