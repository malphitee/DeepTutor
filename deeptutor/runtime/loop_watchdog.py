"""Log what the event loop is doing when it stalls.

Turn owner leases are renewed from the event loop. A synchronous call that
holds the loop past the lease TTL gets the turn failed as ``worker_lost``, and
by the time the loop runs again the culprit has returned and left no trace.
This watchdog thread notices the stall while it is still in progress and logs
the loop thread's current stack, so the blocking call names itself.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

logger = logging.getLogger(__name__)

# Innermost frames only: the blocking call sits at the top of the stack.
_STACK_FRAMES = 40


class EventLoopWatchdog:
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        threshold_seconds: float = 10.0,
        poll_seconds: float = 1.0,
    ) -> None:
        self._loop = loop
        self._threshold_seconds = float(threshold_seconds)
        self._poll_seconds = float(poll_seconds)
        self._loop_thread_id: int | None = None
        self._lock = threading.Lock()
        # Monotonic time the outstanding heartbeat was scheduled; ``None``
        # once the loop has run it.
        self._pending_since: float | None = None
        self._reported = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start watching. Must be called from the loop's own thread."""
        if self._thread is not None:
            return
        self._loop_thread_id = threading.get_ident()
        self._thread = threading.Thread(target=self._run, name="event-loop-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=self._poll_seconds * 2)

    def _beat(self) -> None:
        with self._lock:
            started, reported = self._pending_since, self._reported
            self._pending_since = None
            self._reported = False
        if reported and started is not None:
            logger.warning("Event loop resumed after a %.1fs stall", time.monotonic() - started)

    def _run(self) -> None:
        while not self._stop.wait(self._poll_seconds):
            now = time.monotonic()
            stalled_for = 0.0
            report = False
            with self._lock:
                if self._pending_since is None:
                    self._pending_since = now
                    schedule = True
                else:
                    schedule = False
                    stalled_for = now - self._pending_since
                    report = not self._reported and stalled_for >= self._threshold_seconds
                    if report:
                        self._reported = True
            if schedule:
                try:
                    self._loop.call_soon_threadsafe(self._beat)
                except RuntimeError:
                    # The loop is closed; nothing left to watch.
                    return
            elif report:
                self._report(stalled_for)

    def _report(self, stalled_for: float) -> None:
        frame = sys._current_frames().get(self._loop_thread_id or -1)
        stack = (
            "".join(traceback.format_stack(frame, limit=_STACK_FRAMES))
            if frame is not None
            else "<unavailable>\n"
        )
        logger.warning(
            "Event loop blocked for %.1fs; turn leases cannot renew while it is. "
            "Loop thread stack:\n%s",
            stalled_for,
            stack.rstrip(),
        )


__all__ = ["EventLoopWatchdog"]
