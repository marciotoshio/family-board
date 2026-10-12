"""Database models for Family Board app."""
import sqlite3
import os
import re
from difflib import SequenceMatcher
from datetime import datetime, date, time
from typing import Optional, List
from pydantic import BaseModel

DB_PATH = os.environ.get("DB_PATH", "/app/data/family_board.db")


class Event(BaseModel):
    id: Optional[int] = None
    title: str
    description: str = ""
    date: str  # YYYY-MM-DD
    time: Optional[str] = None  # HH:MM
    end_time: Optional[str] = None
    family_member: str = ""  # a member key from the configured roster (see app/members.py)
    category: str = "event"  # event, school, medical, sport, other
    source: str = "manual"  # manual, email, whatsapp
    google_event_id: Optional[str] = None
    synced: bool = False
    created_at: str = ""


class CashItem(BaseModel):
    id: Optional[int] = None
    title: str
    amount: float = 0.0
    due_date: Optional[str] = None
    paid: bool = False
    family_member: str = ""
    category: str = "school"  # school, field_trip, lunch, other
    source: str = "manual"
    notes: str = ""
    created_at: str = ""


def get_db():
    os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            date TEXT NOT NULL,
            time TEXT,
            end_time TEXT,
            family_member TEXT DEFAULT '',
            category TEXT DEFAULT 'event',
            source TEXT DEFAULT 'manual',
            google_event_id TEXT,
            synced INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS cash_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            amount REAL DEFAULT 0.0,
            due_date TEXT,
            paid INTEGER DEFAULT 0,
            family_member TEXT DEFAULT '',
            category TEXT DEFAULT 'school',
            source TEXT DEFAULT 'manual',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ignored_cash (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT UNIQUE,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS parsed_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT UNIQUE,
            subject TEXT,
            from_addr TEXT,
            received_at TEXT,
            parsed_at TEXT,
            raw_text TEXT,
            success INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()


def add_event(event: Event) -> int:
    conn = get_db()
    dup = _find_duplicate_event(conn, event.title, event.date)
    if dup is not None:
        conn.close()
        return dup["id"]
    cur = conn.execute(
        "INSERT INTO events (title, description, date, time, end_time, family_member, category, source, google_event_id, synced) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event.title, event.description, event.date, event.time, event.end_time,
         event.family_member, event.category, event.source,
         event.google_event_id, int(event.synced))
    )
    conn.commit()
    eid = cur.lastrowid
    conn.close()
    return eid


def _norm_title(title: str) -> str:
    return " ".join((title or "").lower().split())


def _numbers(text: str) -> set:
    return set(re.findall(r"\d+", text))


def is_duplicate_title(a: str, b: str) -> bool:
    """True when two event titles likely describe the same event.

    Titles from different emails often differ slightly (e.g.
    "Thanksgiving - School Closed" vs "Thanksgiving Day - School Closed").
    We merge on high similarity, but never when the numbers differ, so
    "Grade 4 Soccer" and "Grade 5 Soccer" stay distinct."""
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if _numbers(na) != _numbers(nb):
        return False
    return SequenceMatcher(None, na, nb).ratio() >= 0.88


def _find_duplicate_event(conn, title: str, date_str: str):
    for row in conn.execute("SELECT id, title FROM events WHERE date = ?", (date_str,)):
        if is_duplicate_title(row["title"], title):
            return row
    return None


def get_events(start_date: str, end_date: str) -> List[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM events WHERE date >= ? AND date <= ? ORDER BY date, time",
        (start_date, end_date)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Tokens that don't help identify a cash item (months, weeks, filler). Used so
# an ignored "Week of Oct 5 - Oct 9 LUNCH PROGRAM" also suppresses next week's.
_MONTH_TOKENS = {
    "jan","january","feb","february","mar","march","apr","april","may",
    "jun","june","jul","july","aug","august","sep","sept","september",
    "oct","october","nov","november","dec","december",
}
_STOP_TOKENS = _MONTH_TOKENS | {
    "week","of","for","the","a","an","and","to","new","attachment",
    "notification","online","schoolcash","item","reminder","receipt",
}


def _ignore_tokens(title: str) -> set:
    """Distinctive words of a title, dropping digits, month names and filler.

    e.g. "Week of Oct 5 - Oct 9 LUNCH PROGRAM" -> {"lunch", "program"}."""
    words = re.findall(r"[a-z]+", _norm_title(title))
    return {w for w in words if w not in _STOP_TOKENS}


def _is_ignored_cash(conn, title: str) -> bool:
    """True when the title is covered by an ignore rule (all rule words present)."""
    tokens = _ignore_tokens(title)
    if not tokens:
        return False
    for row in conn.execute("SELECT pattern FROM ignored_cash"):
        rule = set((row["pattern"] or "").split())
        if rule and rule <= tokens:
            return True
    return False


def _member_compatible(a: str, b: str) -> bool:
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    return a == b or not a or not b or a == "all" or b == "all"


def _find_duplicate_cash(conn, title: str, member: str):
    """Unpaid item that likely describes the same purchase (same title+member)."""
    for row in conn.execute("SELECT * FROM cash_items WHERE paid = 0"):
        if is_duplicate_title(row["title"], title) and _member_compatible(row["family_member"], member):
            return row
    return None


def _merge_cash(conn, keep_row, amount, due_date, notes, category):
    """Fold a second sighting of a cash item (e.g. a receipt) into the kept row.

    - amount:   keep any known amount; upgrade 0 -> paid amount
    - due_date: keep the earliest known deadline
    - notes:    append anything new
    - category: prefer a specific category over the generic 'school'"""
    sets, params = [], []
    keep_amt = keep_row["amount"] or 0.0
    new_amt = amount or 0.0
    if new_amt > keep_amt:
        sets.append("amount = ?"); params.append(new_amt)

    dates = [d for d in (keep_row["due_date"], due_date) if d]
    if dates and min(dates) != keep_row["due_date"]:
        sets.append("due_date = ?"); params.append(min(dates))

    if notes and notes not in (keep_row["notes"] or ""):
        merged = f"{keep_row['notes']} | {notes}".strip(" |") if keep_row["notes"] else notes
        sets.append("notes = ?"); params.append(merged[:500])

    if (keep_row["category"] or "school") == "school" and category and category != "school":
        sets.append("category = ?"); params.append(category)

    if sets:
        params.append(keep_row["id"])
        conn.execute(f"UPDATE cash_items SET {', '.join(sets)} WHERE id = ?", params)


def add_cash_item(item: CashItem) -> tuple:
    """Insert a cash item, ignoring suppressed titles and merging duplicates.

    Returns (id, created). id is -1 when the item was ignored."""
    conn = get_db()
    if _is_ignored_cash(conn, item.title):
        conn.close()
        return -1, False

    dup = _find_duplicate_cash(conn, item.title, item.family_member)
    if dup is not None:
        _merge_cash(conn, dup, item.amount, item.due_date, item.notes, item.category)
        conn.commit()
        conn.close()
        return dup["id"], False

    cur = conn.execute(
        "INSERT INTO cash_items (title, amount, due_date, paid, family_member, category, source, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (item.title, item.amount, item.due_date, int(item.paid),
         item.family_member, item.category, item.source, item.notes)
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid, True


def dedupe_cash_items() -> int:
    """Merge existing unpaid duplicate cash items (same title + member). Idempotent."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM cash_items WHERE paid = 0 ORDER BY id").fetchall()
    removed = 0
    gone = set()
    for i, row in enumerate(rows):
        if row["id"] in gone:
            continue
        for other in rows[i + 1:]:
            if other["id"] in gone:
                continue
            if is_duplicate_title(row["title"], other["title"]) and _member_compatible(
                row["family_member"], other["family_member"]
            ):
                _merge_cash(conn, row, other["amount"], other["due_date"],
                            other["notes"], other["category"])
                conn.execute("DELETE FROM cash_items WHERE id = ?", (other["id"],))
                gone.add(other["id"])
                removed += 1
    conn.commit()
    conn.close()
    return removed


def ignore_cash_item(item_id: int) -> bool:
    """Delete a cash item and remember its title so future copies are suppressed."""
    conn = get_db()
    row = conn.execute("SELECT * FROM cash_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        conn.close()
        return False
    tokens = _ignore_tokens(row["title"])
    if tokens:
        conn.execute("INSERT OR IGNORE INTO ignored_cash (pattern) VALUES (?)",
                     (" ".join(sorted(tokens)),))
    conn.execute("DELETE FROM cash_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return True


def delete_cash_item(item_id: int) -> bool:
    """Permanently delete a cash item without suppressing its title."""
    conn = get_db()
    cur = conn.execute("DELETE FROM cash_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_ignored_cash() -> List[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM ignored_cash ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def unignore_cash(rule_id: int) -> bool:
    conn = get_db()
    cur = conn.execute("DELETE FROM ignored_cash WHERE id = ?", (rule_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_cash_items(include_paid: bool = False) -> List[dict]:
    conn = get_db()
    query = "SELECT * FROM cash_items" if include_paid else "SELECT * FROM cash_items WHERE paid = 0"
    rows = conn.execute(query + " ORDER BY due_date ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_cash_paid(item_id: int) -> bool:
    conn = get_db()
    conn.execute("UPDATE cash_items SET paid = 1 WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return True


def is_email_processed(email_id: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT id FROM parsed_emails WHERE email_id = ?", (email_id,)).fetchone()
    conn.close()
    return row is not None


def mark_email_processed(email_id: str, subject: str, from_addr: str, raw_text: str, success: bool):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO parsed_emails (email_id, subject, from_addr, received_at, parsed_at, raw_text, success) "
        "VALUES (?, ?, ?, datetime('now'), datetime('now'), ?, ?)",
        (email_id, subject, from_addr, raw_text, int(success))
    )
    conn.commit()
    conn.close()