"""
Poster backup, restore and upload.

This is the ONLY place that actually changes a poster in Jellyfin. All badges
of an item are drawn at once on a clean copy of the original, so the
different badge sources (countdown, languages) never overwrite each other.
"""
from __future__ import annotations

from pathlib import Path

from .badges import compose_badges
from .logbus import log


class PosterService:
    def __init__(self, jf, backup_dir):
        self.jf = jf
        self.backup_dir = Path(backup_dir)
        self._no_poster: set = set()        # titles already reported as having no poster

    def backup_path_for(self, key) -> Path:
        return self.backup_dir / f"{key}.jpg"

    def ensure_original_backup(self, key, jellyfin_id) -> bool:
        bpath = self.backup_path_for(key)
        if bpath.exists():
            return True
        content = self.jf.get_primary_image(jellyfin_id)
        if content is None:
            if key not in self._no_poster:
                self._no_poster.add(key)
                log.info("[Poster] %s has no poster in Jellyfin, so no badge can be drawn on it", key)
            return False
        self._no_poster.discard(key)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        bpath.write_bytes(content)
        return True

    def render_item_badges(self, key, jellyfin_id, badges) -> bool:
        if not self.ensure_original_backup(key, jellyfin_id):
            return False
        composed = compose_badges(self.backup_path_for(key).read_bytes(), badges)
        return self.jf.upload_primary_image(jellyfin_id, composed)

    def restore_original(self, key, jellyfin_id) -> bool:
        """
        Put the original poster back and delete the backup. When the item
        is gone from Jellyfin (or its id is unknown) the backup is just removed.
        """
        bpath = self.backup_path_for(key)
        if not bpath.exists():
            return False
        if jellyfin_id is None or not self.jf.item_exists(jellyfin_id):
            bpath.unlink()
            return False
        if self.jf.upload_primary_image(jellyfin_id, bpath.read_bytes()):
            bpath.unlink()
            log.info("[OK] Restored the original poster of %s", key)
            return True
        return False

    def backup_keys(self) -> list:
        return [p.stem for p in self.backup_dir.glob("*.jpg")] if self.backup_dir.exists() else []
