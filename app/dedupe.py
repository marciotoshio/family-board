"""One-off cleanup for duplicate events (same date + similar title).

Different emails can mention the same event with slightly different wording,
which creates duplicate events both in the local DB and Google Calendar.
This finds those groups and removes the extras.

Run inside the container:
    python -m app.dedupe            # dry run, lists what would change
    python -m app.dedupe --apply    # actually delete
"""
import sys
from collections import defaultdict

from . import database as db


def find_duplicate_groups(rows):
    groups = []
    by_date = defaultdict(list)
    for row in rows:
        by_date[row["date"]].append(row)
    for date_str, items in by_date.items():
        kept = []
        for item in items:
            match = next(
                (k for k in kept if db.is_duplicate_title(k["title"], item["title"])),
                None,
            )
            if match is None:
                kept.append(item)
            else:
                groups.append((match, item))
    return groups


def run(apply: bool = False) -> None:
    conn = db.get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY id")]
    groups = find_duplicate_groups(rows)

    if not groups:
        print("No duplicate events found.")
        conn.close()
        return

    service = None
    removed = 0
    for keep, dup in groups:
        print(f"[{dup['date']}] keep id={keep['id']} '{keep['title']}'  "
              f"<- remove id={dup['id']} '{dup['title']}'")
        if not apply:
            continue
        if dup.get("google_event_id"):
            try:
                if service is None:
                    from . import calendar_sync
                    service = calendar_sync.get_calendar_service()
                    calendar_id = calendar_sync.CALENDAR_ID
                service.events().delete(
                    calendarId=calendar_id, eventId=dup["google_event_id"]
                ).execute()
                print(f"    deleted Google event {dup['google_event_id']}")
            except Exception as e:
                print(f"    Google delete failed for id={dup['id']}: {e}")
        conn.execute("DELETE FROM events WHERE id = ?", (dup["id"],))
        removed += 1

    if apply:
        conn.commit()
    conn.close()
    verb = "Removed" if apply else "Would remove"
    print(f"{verb} {len(groups)} duplicate event(s).")


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
