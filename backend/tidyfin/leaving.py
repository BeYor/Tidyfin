"""Sync of the "Leaving soon" collections in Jellyfin."""
from __future__ import annotations

from .logbus import log


def sync_collection(jf, collection_id: str, desired_ids: set):
    current_ids = jf.collection_member_ids(collection_id)
    to_add, to_remove = desired_ids - current_ids, current_ids - desired_ids

    if to_add:
        log.info("[Collections] Adding to %s: %s", collection_id, sorted(to_add))
        resp = jf.add_to_collection(collection_id, to_add)
        if resp.status_code not in (200, 204):
            log.warning("Bulk add failed: HTTP %s - %s", resp.status_code, resp.text[:200])
            for single_id in to_add:     # find the culprit one by one
                r = jf.add_to_collection(collection_id, [single_id])
                status = "OK" if r.status_code in (200, 204) else f"ERROR HTTP {r.status_code} - {r.text[:150]}"
                log.info("     %s: %s", single_id, status)
    if to_remove:
        resp = jf.remove_from_collection(collection_id, to_remove)
        if resp.status_code not in (200, 204):
            log.warning("Removal from collection %s failed: HTTP %s - %s", collection_id, resp.status_code, resp.text[:200])


def ensure_collection_id(jf, store, configured_id: str, name: str, settings_key: str) -> str:
    """
    Collection id from settings, else found by name, else created. A
    resolved id is remembered in the key/value store (settings stay untouched).
    """
    if configured_id:
        return configured_id
    cached = store.get_json(f"collection_id:{settings_key}")
    if cached and name == (store.get_json(f"collection_name:{settings_key}") or name):
        return cached
    found = jf.find_collection_by_name(name) or jf.create_collection(name)
    if found:
        store.set_json(f"collection_id:{settings_key}", found)
        store.set_json(f"collection_name:{settings_key}", name)
        log.info("[Collections] Using collection '%s' (%s)", name, found)
        return found
    return ""
