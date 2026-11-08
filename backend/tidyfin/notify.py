"""
Notifications: Discord, ntfy, Telegram and a generic JSON webhook.

A failing channel is logged and never breaks a cycle.
"""
from __future__ import annotations

from typing import Optional

from .clients import make_session
from .logbus import log
from .settings import NotifySettings

_TEXT = {
    "en": {
        "title": "Tidyfin", "queued": "Queued for deletion", "deleted": "Deleted", "would_delete": "Would delete (dry run)",
        "failed": "Deletion failed", "error": "Cycle failed", "test": "Test notification - the channel works.",
        "dry": "dry run", "more": "and {n} more", "freed": "freed",
        "conn_lost": "{service} has not answered for {n} cycles in a row. Tidyfin leaves everything about it alone until it does.",
        "conn_back": "{service} answers again.",
    },
    "pl": {
        "title": "Tidyfin", "queued": "Dodano do kolejki usunięć", "deleted": "Usunięto", "would_delete": "Usunięto by (tryb testowy)",
        "failed": "Nie udało się usunąć", "error": "Cykl zakończył się błędem", "test": "Powiadomienie testowe - kanał działa.",
        "dry": "tryb testowy", "more": "i jeszcze {n}", "freed": "zwolniono",
        "conn_lost": "{service} nie odpowiada od {n} cykli z rzędu. Tidyfin nie rusza niczego, co z nim związane, dopóki nie wróci.",
        "conn_back": "{service} znowu odpowiada.",
    },
}


def _size(n: int) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000
    return ""


def format_events(locale: str, events: list, dry_run: bool, cfg: NotifySettings) -> Optional[tuple]:
    """
    Build (title, body) from cycle events honoring the enabled event types;
    None when there is nothing to report.
    """
    t = _TEXT.get(locale, _TEXT["en"])
    groups = []
    if cfg.on_queued:
        groups.append(("queued", t["queued"]))
    if cfg.on_deleted:
        groups.append(("deleted", t["deleted"]))
        groups.append(("would_delete", t["would_delete"]))
    if cfg.on_errors:
        groups.append(("delete_failed", t["failed"]))
    lines = []
    for kind, label in groups:
        items = [e for e in events if e["type"] == kind]
        if not items:
            continue
        total = sum(e.get("size_bytes", 0) for e in items)
        lines.append(f"{label}: {len(items)}" + (f" ({_size(total)})" if total else ""))
        for e in items[:10]:
            lines.append(f"  - {e['title']}")
        if len(items) > 10:
            lines.append("  - " + t["more"].format(n=len(items) - 10))
    if not lines:
        return None
    title = t["title"] + (f" ({t['dry']})" if dry_run else "")
    return title, "\n".join(lines)


class Notifier:
    def __init__(self, cfg: NotifySettings, session=None, timeout: int = 10):
        self.cfg = cfg
        self.http = session or make_session(timeout)

    def _discord(self, title, body):
        r = self.http.post(self.cfg.discord_url, json={"content": f"**{title}**\n{body}"[:1900]})
        return r.status_code in (200, 204)

    def _ntfy(self, title, body):
        headers = {"Title": "Tidyfin", "Tags": "broom"}   # header values must be latin-1, so the title stays ASCII
        if self.cfg.ntfy_token:
            headers["Authorization"] = f"Bearer {self.cfg.ntfy_token}"
        r = self.http.post(self.cfg.ntfy_url, data=f"{title}\n{body}".encode("utf-8"), headers=headers)
        return r.status_code == 200

    def _telegram(self, title, body):
        r = self.http.post(f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendMessage",
                           json={"chat_id": self.cfg.telegram_chat_id, "text": f"{title}\n{body}"[:4000]})
        return r.status_code == 200

    def _webhook(self, title, body, payload):
        r = self.http.post(self.cfg.webhook_url, json={"app": "tidyfin", "title": title, "message": body, **(payload or {})})
        return 200 <= r.status_code < 300

    def send(self, title: str, body: str, payload: Optional[dict] = None) -> dict:
        """Send to every configured channel. Returns {channel: ok | error text}."""
        results = {}
        for channel in self.cfg.channels:
            try:
                ok = (self._webhook(title, body, payload) if channel == "webhook"
                      else getattr(self, f"_{channel}")(title, body))
                results[channel] = True if ok else "rejected"
            except Exception as e:   # network errors, bad URLs...
                results[channel] = str(e)[:200]
            if results[channel] is not True:
                log.warning("[Notify] %s failed: %s", channel, results[channel])
        return results

    def send_test(self, locale: str) -> dict:
        return self.send("Tidyfin", _TEXT.get(locale, _TEXT["en"])["test"], {"event": "test"})


def notify_cycle(settings, events: list, summary: dict, session=None):
    """Called at the end of a cycle."""
    cfg = settings.notifications
    if not cfg.enabled or not cfg.channels:
        return
    msg = format_events(settings.locale, events, settings.dry_run, cfg)
    if msg:
        Notifier(cfg, session).send(msg[0], msg[1], {"event": "cycle", "dry_run": settings.dry_run, "summary": summary,
                                                      "events": events[:50]})


def notify_error(settings, error: str, session=None):
    cfg = settings.notifications
    if not cfg.enabled or not cfg.on_errors or not cfg.channels:
        return
    t = _TEXT.get(settings.locale, _TEXT["en"])
    Notifier(cfg, session).send(t["title"], f"{t['error']}: {error}"[:500], {"event": "error", "error": error})


def notify_connection(settings, service: str, cycles: int, lost: bool, session=None):
    """Radarr, Sonarr or Jellyfin stopped answering (or answers again)."""
    cfg = settings.notifications
    if not cfg.enabled or not cfg.on_errors or not cfg.channels:
        return
    t = _TEXT.get(settings.locale, _TEXT["en"])
    text = t["conn_lost"] if lost else t["conn_back"]
    Notifier(cfg, session).send(t["title"], text.format(service=service.capitalize(), n=cycles),
                                {"event": "connection_lost" if lost else "connection_back", "service": service})
