"""The one background thread: an asyncio loop that owns every Playwright object.

The GUI talks to it only through two queues: commands in (`submit`), events out (the queue the
GUI drains with root.after). The worker never touches Tk.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

from .errors import describe
from .sessions import SessionManager

log = logging.getLogger(__name__)

ManagerFactory = Callable[[Callable[..., None]], SessionManager]


class WorkerNotRunningError(RuntimeError):
    """The background helper that drives the browsers has stopped or never started."""


class Worker:
    """Runs a SessionManager on its own thread and loop."""

    def __init__(self, events: queue.Queue[tuple[str, Any]], manager_factory: ManagerFactory = SessionManager) -> None:
        self.events = events
        self._factory = manager_factory
        self._thread = threading.Thread(target=self._run, name="fanout-worker", daemon=True)
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._commands: asyncio.Queue[tuple[str, dict[str, Any]]] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self.final_snapshot: dict[str, Any] = {}

    def emit(self, kind: str, **data: Any) -> None:
        """Send an event to the GUI. Safe to call from the worker thread."""
        self.events.put((kind, data))

    def start(self) -> None:
        """Start the thread (once)."""
        if not self._thread.is_alive():
            self._thread.start()

    def submit(self, command: str, **payload: Any) -> None:
        """Queue a command for the worker. Safe to call from the GUI thread."""
        if not self._ready.wait(timeout=10) or self._loop is None or self._commands is None or not self._thread.is_alive():
            raise WorkerNotRunningError("FanOut's browser helper isn't running")
        self._loop.call_soon_threadsafe(self._commands.put_nowait, (command, payload))

    def shutdown(self, timeout: float = 20.0) -> bool:
        """Close every browser and stop the thread. True if it finished in time."""
        if not self._thread.is_alive():
            return True
        try:
            self.submit("shutdown")
        except RuntimeError:
            return False
        self._thread.join(timeout)
        return not self._thread.is_alive()

    # ----- worker thread --------------------------------------------------------------------

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception:  # logged with its traceback; the GUI learns of it through submit() failing at once
            log.exception("The browser helper stopped")
        finally:
            self._loop = None
            self._ready.set()  # never leave submit() waiting for a helper that is gone

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._commands = asyncio.Queue()
        manager = self._factory(self.emit)
        self._ready.set()
        while True:
            command, payload = await self._commands.get()
            if command == "shutdown":
                break
            if command == "cancel_run":
                for running in list(self._tasks):
                    if running.get_name() == "send_all":
                        running.cancel()
                continue
            task = asyncio.create_task(self._dispatch(manager, command, payload), name=command)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self.final_snapshot = await manager.shutdown()

    async def _dispatch(self, manager: SessionManager, command: str, payload: dict[str, Any]) -> None:
        handlers: dict[str, Callable[..., Any]] = {
            "open_windows": manager.open_all,
            "close_windows": manager.close_all,
            "close_brave": manager.close_user_brave,
            "map_account": manager.map_account,
            "pick_element": manager.pick_element,
            "send_all": manager.send_all,
        }
        try:
            if command == "snapshot":
                await self._snapshot(manager)
            elif command in handlers:
                await handlers[command](**payload)
            else:
                log.warning("Ignoring unknown command %r", command)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            friendly = describe(exc)
            self.emit("worker_error", message=friendly.message, fix=friendly.fix, command=command)

    async def _snapshot(self, manager: SessionManager) -> None:
        self.emit("snapshot", **await manager.snapshot())
