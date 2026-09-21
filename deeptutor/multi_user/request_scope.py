"""ASGI boundary: no request inherits a deployment or a previous user's scope."""

from .context import _current_user, _request_active


class UserScopeMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
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
                    pass
            _current_user.reset(user)
            _request_active.reset(active)
