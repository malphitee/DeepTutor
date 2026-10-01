from __future__ import annotations

import asyncio
import logging
import time

import pytest

from deeptutor.runtime.loop_watchdog import EventLoopWatchdog


def _synchronous_vocabulary_download() -> None:
    time.sleep(0.6)


@pytest.mark.asyncio
async def test_a_blocked_loop_logs_the_call_holding_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    watchdog = EventLoopWatchdog(
        asyncio.get_running_loop(), threshold_seconds=0.2, poll_seconds=0.05
    )
    watchdog.start()
    try:
        with caplog.at_level(logging.WARNING, logger="deeptutor.runtime.loop_watchdog"):
            await asyncio.sleep(0.1)
            _synchronous_vocabulary_download()
            await asyncio.sleep(0.1)
    finally:
        watchdog.stop()

    messages = [record.getMessage() for record in caplog.records]
    blocked = [message for message in messages if message.startswith("Event loop blocked")]
    assert len(blocked) == 1
    assert "_synchronous_vocabulary_download" in blocked[0]
    assert any(message.startswith("Event loop resumed") for message in messages)


@pytest.mark.asyncio
async def test_a_responsive_loop_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    watchdog = EventLoopWatchdog(
        asyncio.get_running_loop(), threshold_seconds=0.2, poll_seconds=0.05
    )
    watchdog.start()
    try:
        with caplog.at_level(logging.WARNING, logger="deeptutor.runtime.loop_watchdog"):
            for _ in range(10):
                await asyncio.sleep(0.03)
    finally:
        watchdog.stop()

    assert caplog.records == []
