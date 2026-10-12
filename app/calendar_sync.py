"""Google Calendar sync — pushes local events to Google Calendar."""
import os
import json
from datetime import datetime
from typing import Optional

import httpx
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import database as db

CALENDAR_ID = os.environ.get("GCAL_ID", "primary")


def get_calendar_service():
    """Authenticate and return Calendar API service."""
    token_path = os.environ.get("GOOGLE_TOKEN", "/app/google_token.json")
    if not os.path.exists(token_path):
        print("WARN: No Google token found")
        return None
    with open(token_path) as f:
        creds = Credentials.from_authorized_user_info(json.load(f))
    return build("calendar", "v3", credentials=creds)


def sync_events():
    """Push unsynced events to Google Calendar. Return count."""
    service = get_calendar_service()
    if not service:
        return 0

    synced = 0
    conn = db.get_db()
    rows = conn.execute(
        "SELECT * FROM events WHERE (synced = 0 OR google_event_id IS NULL) AND date >= date('now', '-1 day')"
    ).fetchall()
    conn.close()

    for row in rows:
        ev = dict(row)
        try:
            start = {"date": ev["date"]}  # all-day by default
            if ev.get("time"):
                start = {
                    "dateTime": f"{ev['date']}T{ev['time']}:00",
                    "timeZone": "America/Toronto",
                }
            end = start.copy()
            if ev.get("end_time"):
                end["dateTime"] = f"{ev['date']}T{ev['end_time']}:00"

            body = {
                "summary": f"[{ev['family_member'].upper()}] {ev['title']}" if ev['family_member'] else ev['title'],
                "description": ev.get("description", ""),
                "start": start,
                "end": end,
                "source": "family-board",
            }
            if ev.get("google_event_id"):
                # Update
                service.events().update(calendarId=CALENDAR_ID, eventId=ev["google_event_id"], body=body).execute()
            else:
                created = service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
                gid = created.get("id")
                if gid:
                    conn = db.get_db()
                    conn.execute("UPDATE events SET google_event_id = ?, synced = 1 WHERE id = ?",
                                 (gid, ev["id"]))
                    conn.commit()
                    conn.close()
            synced += 1
        except HttpError as e:
            print(f"Google Calendar sync error for event {ev['id']}: {e}")

    return synced


def fetch_remote_events(days_back: int = 7, days_forward: int = 60):
    """Pull events from Google Calendar into local DB (avoid duplicates)."""
    service = get_calendar_service()
    if not service:
        return 0

    from datetime import timedelta, timezone
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days_back)).isoformat()
    time_max = (now + timedelta(days=days_forward)).isoformat()

    fetched = 0
    try:
        events = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        for ev in events.get("items", []):
            gid = ev.get("id", "")
            # Check if already in local DB
            conn = db.get_db()
            existing = conn.execute("SELECT id FROM events WHERE google_event_id = ?", (gid,)).fetchone()
            if existing:
                conn.close()
                continue

            # Parse the event
            summary = ev.get("summary", "")
            start_info = ev.get("start", {})
            date_str = start_info.get("date") or start_info.get("dateTime", "")[:10]
            time_str = None
            if "dateTime" in start_info:
                time_str = start_info["dateTime"][11:16]

            # Try to extract family member from summary brackets
            fm = "all"
            if summary.startswith("["):
                close = summary.find("]")
                if close > 0:
                    fm = summary[1:close].lower()
                    summary = summary[close+1:].strip()

            event = db.Event(
                title=summary,
                description=ev.get("description", ""),
                date=date_str,
                time=time_str,
                family_member=fm,
                source="google_sync",
                google_event_id=gid,
                synced=True,
            )
            db.add_event(event)
            fetched += 1
            conn.close()

    except HttpError as e:
        print(f"Google Calendar fetch error: {e}")

    return fetched