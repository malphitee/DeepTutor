"""Feishu/Lark SDK initialization contract tests."""

from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest

from deeptutor.partners.channels import feishu as feishu_mod
from deeptutor.partners.channels.feishu import FeishuChannel


class _Chain:
    def __init__(self, calls: list[tuple[str, Any]], result: Any) -> None:
        self.calls = calls
        self.result = result

    def __getattr__(self, name: str) -> Any:
        def method(value: Any = None) -> "_Chain":
            self.calls.append((name, value))
            return self

        return method

    def build(self) -> Any:
        self.calls.append(("build", None))
        return self.result


@pytest.mark.asyncio
async def test_feishu_channel_selects_domain_for_rest_and_websocket_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rest_calls: list[tuple[str, Any]] = []
    event_calls: list[tuple[str, Any]] = []
    ws_clients: list[dict[str, Any]] = []

    class Client:
        @staticmethod
        def builder() -> _Chain:
            return _Chain(rest_calls, object())

    class EventDispatcherHandler:
        @staticmethod
        def builder(*args: Any) -> _Chain:
            return _Chain(event_calls, object())

    class WSClient:
        def __init__(
            self, *args: Any, extra_ua_tags: list[str] | None = None, **kwargs: Any
        ) -> None:
            ws_clients.append({"args": args, "kwargs": {**kwargs, "extra_ua_tags": extra_ua_tags}})

    lark = SimpleNamespace(
        Client=Client,
        EventDispatcherHandler=EventDispatcherHandler,
        LogLevel=SimpleNamespace(INFO="INFO"),
        ws=SimpleNamespace(Client=WSClient, client=SimpleNamespace()),
    )
    const = SimpleNamespace(FEISHU_DOMAIN="FEISHU_DOMAIN", LARK_DOMAIN="LARK_DOMAIN")

    monkeypatch.setitem(__import__("sys").modules, "lark_oapi", lark)
    monkeypatch.setitem(__import__("sys").modules, "lark_oapi.ws", lark.ws)
    monkeypatch.setitem(__import__("sys").modules, "lark_oapi.core", SimpleNamespace())
    monkeypatch.setitem(__import__("sys").modules, "lark_oapi.core.const", const)
    monkeypatch.setattr(feishu_mod, "FEISHU_AVAILABLE", True)
    monkeypatch.setattr(
        feishu_mod,
        "threading",
        SimpleNamespace(Thread=lambda *args, **kwargs: SimpleNamespace(start=lambda: None)),
    )

    async def exercise(domain: str) -> None:
        rest_calls.clear()
        ws_clients.clear()
        channel = FeishuChannel(
            {
                "enabled": True,
                "app_id": "app-id",
                "app_secret": "app-secret",
                "domain": domain,
            },
            object(),
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(channel.start(), timeout=0.05)

    await exercise("lark")
    assert ("domain", "LARK_DOMAIN") in rest_calls
    assert ws_clients[0]["kwargs"]["domain"] == "LARK_DOMAIN"
    assert ws_clients[0]["kwargs"]["extra_ua_tags"] == ["channel"]

    await exercise("feishu")
    assert ("domain", "FEISHU_DOMAIN") in rest_calls
    assert ws_clients[0]["kwargs"]["domain"] == "FEISHU_DOMAIN"
    assert ws_clients[0]["kwargs"]["extra_ua_tags"] == ["channel"]

    class LegacyWSClient:
        def __init__(
            self,
            *args: Any,
            event_handler: Any = None,
            log_level: Any = None,
            domain: str = "",
        ) -> None:
            ws_clients.append(
                {
                    "args": args,
                    "kwargs": {
                        "event_handler": event_handler,
                        "log_level": log_level,
                        "domain": domain,
                    },
                }
            )

    lark.ws.Client = LegacyWSClient
    await exercise("feishu")
    assert ws_clients[0]["kwargs"]["domain"] == "FEISHU_DOMAIN"
    assert "extra_ua_tags" not in ws_clients[0]["kwargs"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_first_attempt", [False, True])
async def test_delayed_feishu_listener_start_and_retry_do_not_stay_connecting(
    monkeypatch: pytest.MonkeyPatch, fail_first_attempt: bool
) -> None:
    """The blocking SDK has no readiness event; each live listener reads running."""
    listener_started = asyncio.Event()
    threads: list[Any] = []
    attempt_states: list[dict[str, str]] = []
    retry_states: list[dict[str, str]] = []

    class Client:
        @staticmethod
        def builder() -> _Chain:
            return _Chain([], object())

    class EventDispatcherHandler:
        @staticmethod
        def builder(*_args: Any) -> _Chain:
            return _Chain([], object())

    class WSClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def start(self) -> None:
            attempt_states.append(channel.setup_state)
            if fail_first_attempt and len(attempt_states) == 1:
                raise RuntimeError("private-credential-must-not-be-published")
            channel._running = False

    class DeferredThread:
        def __init__(self, *, target: Any, daemon: bool) -> None:
            self.target = target
            threads.append(self)

        def start(self) -> None:
            # Let the async caller finish its startup work before the listener
            # thread begins, reproducing the former status-publication race.
            listener_started.set()

    sdk_client_module = SimpleNamespace()
    lark = SimpleNamespace(
        Client=Client,
        EventDispatcherHandler=EventDispatcherHandler,
        LogLevel=SimpleNamespace(INFO="INFO"),
        ws=SimpleNamespace(Client=WSClient, client=sdk_client_module),
    )
    monkeypatch.setitem(sys.modules, "lark_oapi", lark)
    monkeypatch.setitem(sys.modules, "lark_oapi.ws", lark.ws)
    monkeypatch.setitem(sys.modules, "lark_oapi.ws.client", sdk_client_module)
    monkeypatch.setitem(sys.modules, "lark_oapi.core", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "lark_oapi.core.const",
        SimpleNamespace(FEISHU_DOMAIN="FEISHU_DOMAIN", LARK_DOMAIN="LARK_DOMAIN"),
    )
    monkeypatch.setattr(feishu_mod, "FEISHU_AVAILABLE", True)
    monkeypatch.setattr(feishu_mod, "threading", SimpleNamespace(Thread=DeferredThread))
    monkeypatch.setattr(time, "sleep", lambda _seconds: retry_states.append(channel.setup_state))
    channel = FeishuChannel({"app_id": "app-id", "app_secret": "app-secret"}, object())
    channel.set_setup_state("connecting")
    startup = asyncio.create_task(channel.start())
    event_loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(listener_started.wait(), timeout=1)
        threads[0].target()
        assert attempt_states == [{"status": "running"}] * (2 if fail_first_attempt else 1)
        assert retry_states == (
            [
                {
                    "status": "error",
                    "message": "Channel connection failed; the listener will retry.",
                }
            ]
            if fail_first_attempt
            else []
        )
        assert channel.setup_state == {"status": "running"}
    finally:
        # The deferred target uses the thread's real loop setup in this test
        # thread, so restore its event-loop policy before leaving the fixture.
        asyncio.set_event_loop(event_loop)
        await channel.stop()
        startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)
