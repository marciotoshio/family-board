"""Read the shared shopping list + family message from Google Keep.

Google Keep has no public API, so this uses the unofficial `gkeepapi`. The
board logs into the shared family account and reads two notes (titles
configurable via KEEP_SHOPPING_NOTE / KEEP_MESSAGE_NOTE). Input happens in the
Google apps (Nest/Assistant "add to shopping list", Gemini, Keep on phones);
the board only reads.

Auth uses a master token created once with `get_keep_token.py` and stored in
KEEP_MASTER_TOKEN (with KEEP_EMAIL). If it is missing or fails, the board
degrades gracefully and the sidebar shows an "unavailable" placeholder.
"""
import os
import time
import threading

import gkeepapi

_LOCK = threading.Lock()
_cache = {
    "shopping": [],
    "message": "",
    "available": False,
    "error": None,
    "updated": 0.0,
}


def _find_note(keep, title: str):
    want = title.strip().lower()
    for note in keep.all():
        if (note.title or "").strip().lower() == want and not note.trashed:
            return note
    return None


def _note_text(note) -> str:
    if note is None:
        return ""
    items = getattr(note, "items", None)
    if items:
        return "\n".join(i.text for i in items if i.text and i.text.strip())
    return (note.text or "").strip()


def _shopping_from_note(note) -> list:
    if note is None:
        return []
    items = getattr(note, "items", None)
    if items:
        return [
            {"text": it.text.strip(), "checked": bool(it.checked)}
            for it in items if it.text and it.text.strip()
        ]
    return [
        {"text": line.strip(), "checked": False}
        for line in (note.text or "").splitlines() if line.strip()
    ]


def refresh():
    """Sync Keep and update the cache. Keeps previous data on failure."""
    email = os.environ.get("KEEP_EMAIL", "").strip()
    token = os.environ.get("KEEP_MASTER_TOKEN", "").strip()
    if not email or not token:
        with _LOCK:
            _cache.update(available=False,
                          error="KEEP_EMAIL / KEEP_MASTER_TOKEN not configured")
        return
    try:
        keep = gkeepapi.Keep()
        keep.authenticate(email, token)
        shopping = _shopping_from_note(
            _find_note(keep, os.environ.get("KEEP_SHOPPING_NOTE", "Shopping list")))
        message = _note_text(
            _find_note(keep, os.environ.get("KEEP_MESSAGE_NOTE", "Family Board")))
        with _LOCK:
            _cache.update(shopping=shopping, message=message,
                          available=True, error=None, updated=time.time())
    except Exception as e:
        with _LOCK:
            _cache.update(available=False, error=f"{type(e).__name__}: {e}")


def get() -> dict:
    with _LOCK:
        return dict(_cache)
