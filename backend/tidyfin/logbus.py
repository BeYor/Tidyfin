"""Application logging: stdout, in-memory buffer, and dashboard subscriptions via SSE."""
from __future__ import annotations

import collections
import itertools
import logging
import queue
import sys
import threading
from datetime import datetime, timezone

log = logging.getLogger("tidyfin")


class LogBus(logging.Handler):
    def __init__(self, capacity: int = 2000):
        super().__init__()
        self._buf = collections.deque(maxlen=capacity)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []

    def emit(self, record: logging.LogRecord):
        try:
            entry = {
                "id": next(self._ids),
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "level": record.levelname,
                "msg": record.getMessage(),
            }
        except Exception:
            return
        with self._lock:
            self._buf.append(entry)
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(entry)
            except queue.Full:
                pass

    def recent(self, limit: int = 200, after_id: int = 0) -> list[dict]:
        with self._lock:
            items = [e for e in self._buf if e["id"] > after_id]
        return items[-limit:]

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)


bus = LogBus()


def setup_logging(level: int = logging.INFO):
    log.setLevel(level)
    log.propagate = False
    if bus not in log.handlers:
        log.addHandler(bus)
    if not any(isinstance(h, logging.StreamHandler) and getattr(h, "_tidyfin", False) for h in log.handlers):
        h = logging.StreamHandler(sys.stdout)
        h._tidyfin = True  # type: ignore[attr-defined]
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
