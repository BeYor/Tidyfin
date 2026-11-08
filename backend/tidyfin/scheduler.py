"""Scheduler: a background thread that runs a cycle every N seconds or on demand."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .logbus import log
from .notify import notify_error
from .settings import Settings, load_settings


class Scheduler:
    def __init__(self, store, cycle_runner: Callable[[Settings], dict]):
        """``cycle_runner(settings) -> summary`` is called from the worker thread."""
        self.store = store
        self.cycle_runner = cycle_runner
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._run_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._pending: Optional[dict] = None
        self.running = False
        self.next_run_at: Optional[datetime] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="tidyfin-scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def trigger(self, dry_run: Optional[bool] = None) -> bool:
        """Request an immediate cycle. False if one is already running."""
        if self.running:
            return False
        self._pending = {"trigger": "manual", "dry_run": dry_run}
        self._wake.set()
        return True

    def status(self) -> dict:
        return {"running": self.running,
                "next_run_at": self.next_run_at.isoformat(timespec="seconds") if self.next_run_at else None}

    def _loop(self):
        first = True
        while not self._stop.is_set():
            interval = load_settings(self.store).run_interval_seconds
            if not first:
                self.next_run_at = datetime.now(timezone.utc) + timedelta(seconds=interval)
                self._wake.wait(timeout=interval)
            first = False
            if self._stop.is_set():
                break
            pending, self._pending = self._pending, None
            self._wake.clear()
            self.run_once(trigger=(pending or {}).get("trigger", "schedule"),
                          dry_run_override=(pending or {}).get("dry_run"))

    def run_once(self, trigger: str = "schedule", dry_run_override: Optional[bool] = None) -> Optional[dict]:
        if not self._run_lock.acquire(blocking=False):
            return None
        self.running = True
        run_id, settings = None, None
        try:
            settings = load_settings(self.store)
            if not settings.setup_complete:
                log.info("Setup is not complete - skipping the cycle")
                return None
            if not settings.jellyfin.configured:
                log.warning("No Jellyfin connection - skipping the cycle")
                return None
            if dry_run_override is not None:
                settings = settings.with_overrides(dry_run=dry_run_override)
            run_id = self.store.start_run(trigger, settings.dry_run)
            log.info("=== starting a cycle (%s%s) ===", trigger, ", DRY RUN" if settings.dry_run else "")
            started = time.monotonic()
            summary = self.cycle_runner(settings)
            summary["duration_seconds"] = round(time.monotonic() - started, 1)
            self.store.finish_run(run_id, "ok", summary)
            log.info("=== cycle finished in %.1fs ===", summary["duration_seconds"])
            return summary
        except Exception as e:      # a failing cycle must not kill the worker thread
            log.exception("Unexpected cycle error: %s", e)
            if run_id is not None:
                self.store.finish_run(run_id, "error", None, str(e))
            if settings is not None:
                try:
                    notify_error(settings, str(e))
                except Exception:
                    pass
            return None
        finally:
            self.running = False
            self._run_lock.release()
