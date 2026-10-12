"""Family Board — Smart Calendar for Fire Tablet Kiosk"""
import os
import json
import re
import hashlib
import threading
from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler

from . import database as db
from . import gmail_scraper
from . import calendar_sync
from . import weather
from . import keep_source
from . import members

app = FastAPI(title="Family Board", version="1.0.0")

# Init DB on startup
db.init_db()
try:
    db.dedupe_cash_items()
except Exception as _e:
    print("cash dedupe:", _e)

# Serve static frontend
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def asset_version() -> str:
    """Fingerprint of the static assets, used to bust kiosk browser caches.

    Recomputed per call so it also reflects changes made under a bind mount
    without needing a restart."""
    h = hashlib.md5()
    for name in sorted(os.listdir(static_dir)):
        path = os.path.join(static_dir, name)
        if os.path.isfile(path):
            st = os.stat(path)
            h.update(f"{name}:{st.st_mtime_ns}:{st.st_size}".encode())
    return h.hexdigest()[:12]


@app.middleware("http")
async def no_cache(request: Request, call_next):
    """Force the kiosk WebView to revalidate HTML/static on every load so
    new deploys are picked up instead of serving a stale cached version."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ─── Background Scheduler ───
scheduler = BackgroundScheduler()

# How often the background cycle runs (scrape Gmail + push/pull Google Calendar).
# Local events are only created for *new* unread email, so running more often
# costs no extra LLM credits — just Gmail API calls.
SYNC_INTERVAL_HOURS = float(os.environ.get("SYNC_INTERVAL_HOURS", "2"))

# How often the sidebar sources (weather, Google Keep) are refreshed.
SIDEBAR_REFRESH_MINUTES = int(os.environ.get("SIDEBAR_REFRESH_MINUTES", "10"))


@scheduler.scheduled_job("interval", hours=SYNC_INTERVAL_HOURS, id="sync_cycle",
                         max_instances=1, coalesce=True)
def scheduled_sync():
    """Periodically scrape emails + sync calendars."""
    print(f"[{datetime.now()}] Scheduled sync cycle running...")
    try:
        ev, ci = gmail_scraper.scrape_emails()
        print(f"  Scraped: {ev} events, {ci} cash items")
    except Exception as e:
        print(f"  Scrape error: {e}")
    try:
        synced = calendar_sync.sync_events()
        print(f"  Synced {synced} events")
    except Exception as e:
        print(f"  Sync error: {e}")
    try:
        fetched = calendar_sync.fetch_remote_events()
        print(f"  Fetched {fetched} events")
    except Exception as e:
        print(f"  Fetch error: {e}")


@scheduler.scheduled_job("interval", minutes=SIDEBAR_REFRESH_MINUTES, id="sidebar_refresh",
                         max_instances=1, coalesce=True)
def scheduled_sidebar():
    """Refresh weather + Google Keep (shopping list / family message)."""
    weather.refresh()
    keep_source.refresh()


@app.on_event("startup")
def start_scheduler():
    try:
        scheduler.start()
        print("Background scheduler started")
    except Exception as e:
        print(f"Scheduler start: {e}")
    # Prime the sidebar caches without blocking startup.
    threading.Thread(target=scheduled_sidebar, daemon=True).start()


# ─── API Models ───

class EventCreate(BaseModel):
    title: str
    description: str = ""
    date: str
    time: Optional[str] = None
    end_time: Optional[str] = None
    family_member: str = "all"
    category: str = "event"


class CashCreate(BaseModel):
    title: str
    amount: float = 0.0
    due_date: Optional[str] = None
    family_member: str = "all"
    category: str = "school"
    notes: str = ""


class WhatsAppMessage(BaseModel):
    from_number: str = ""
    body: str = ""


# ─── Routes ───

@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat()}


@app.get("/")
def serve_kiosk():
    html = open(os.path.join(static_dir, "index.html")).read()
    v = asset_version()
    html = html.replace("/static/app.js", f"/static/app.js?v={v}")
    html = html.replace("/static/style.css", f"/static/style.css?v={v}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/api/config")
def get_config():
    """Frontend-safe runtime configuration (app title + family roster).

    The kiosk renders its member tabs, dropdowns, labels and colours from this,
    so renaming/adding members needs no frontend redeploy."""
    return members.public_config()


@app.get("/api/version")
def version():
    """Returns the current static-asset fingerprint. The kiosk polls this and
    reloads itself when it changes, so a new deploy appears without a manual
    refresh on the tablet."""
    return {"version": asset_version()}


@app.get("/api/events")
def list_events(
    start: str = Query(default=None),
    end: str = Query(default=None),
    member: str = Query(default=None),
):
    if not start:
        start = date.today().isoformat()
    if not end:
        end = (date.today() + timedelta(days=30)).isoformat()

    events = db.get_events(start, end)
    if member and member != "all":
        events = [e for e in events if e["family_member"] in (member, "all", "")]
    return events


@app.post("/api/events")
def create_event(ev: EventCreate):
    event = db.Event(
        title=ev.title, description=ev.description,
        date=ev.date, time=ev.time, end_time=ev.end_time,
        family_member=ev.family_member, category=ev.category, source="manual",
    )
    eid = db.add_event(event)
    try:
        calendar_sync.sync_events()
    except Exception:
        pass
    return {"id": eid, "status": "created"}


@app.get("/api/cash")
def list_cash(include_paid: bool = Query(default=False)):
    return db.get_cash_items(include_paid=include_paid)


@app.post("/api/cash")
def create_cash(ci: CashCreate):
    item = db.CashItem(
        title=ci.title, amount=ci.amount, due_date=ci.due_date,
        family_member=ci.family_member, category=ci.category,
        notes=ci.notes, source="manual",
    )
    cid, created = db.add_cash_item(item)
    if cid == -1:
        return {"id": -1, "status": "ignored"}
    return {"id": cid, "status": "created" if created else "merged"}


@app.get("/api/cash/ignored")
def list_ignored_cash():
    return db.get_ignored_cash()


@app.delete("/api/cash/ignored/{rule_id}")
def remove_ignored_cash(rule_id: int):
    if db.unignore_cash(rule_id):
        return {"status": "deleted"}
    raise HTTPException(404, "Rule not found")


@app.post("/api/cash/{item_id}/pay")
def mark_paid(item_id: int):
    if db.mark_cash_paid(item_id):
        return {"status": "paid"}
    raise HTTPException(404, "Item not found")


@app.post("/api/cash/{item_id}/ignore")
def ignore_cash(item_id: int):
    """Remove a cash item and suppress future items with the same title."""
    if db.ignore_cash_item(item_id):
        return {"status": "ignored"}
    raise HTTPException(404, "Item not found")


@app.delete("/api/cash/{item_id}")
def delete_cash(item_id: int):
    """Delete a cash item without adding an ignore rule."""
    if db.delete_cash_item(item_id):
        return {"status": "deleted"}
    raise HTTPException(404, "Item not found")


@app.post("/api/scrape")
def trigger_scrape():
    ev, ci = gmail_scraper.scrape_emails()
    return {"events_created": ev, "cash_created": ci}


@app.post("/api/sync")
def trigger_sync():
    synced = calendar_sync.sync_events()
    fetched = calendar_sync.fetch_remote_events()
    return {"synced_to_google": synced, "fetched_from_google": fetched}


@app.get("/api/overview")
def get_overview(days: int = Query(default=7)):
    today = date.today()
    start = today.isoformat()
    end = (today + timedelta(days=days)).isoformat()

    events = db.get_events(start, end)
    cash = db.get_cash_items(include_paid=False)

    by_date = {}
    for ev in events:
        d = ev["date"]
        if d not in by_date:
            by_date[d] = []
        by_date[d].append(ev)

    members = set()
    for ev in events:
        if ev["family_member"] and ev["family_member"] != "all":
            members.add(ev["family_member"])

    sidebar = keep_source.get()
    return {
        "today": today.isoformat(),
        "total_events": len(events),
        "total_cash_due": len(cash),
        "total_cash_amount": sum(c["amount"] for c in cash),
        "by_date": by_date,
        "family_members": sorted(members),
        "cash_items": cash[:30],
        "weather": weather.get(),
        "shopping": sidebar["shopping"],
        "message": sidebar["message"],
    }


@app.post("/api/whatsapp")
def whatsapp_webhook(msg: WhatsAppMessage):
    """Receive WhatsApp messages and parse into events/cash items.
    Format: 📅 Jamie soccer Wed 4pm  or  💰 Field trip $25 due May 10"""
    body = msg.body.strip()

    if body.startswith("\uD83D\uDCC5") or body.startswith("/add") or body.startswith("add "):
        parts = body.replace("\uD83D\uDCC5", "").replace("/add", "add").strip()
        title = parts
        member = members.match_member(title) or "all"
        if member != "all":
            title = title.lower().replace(member, "").strip()

        date_match = re.search(r'(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)', title)
        event_date = date.today().isoformat()
        if date_match:
            from dateutil import parser as dateparser
            try:
                parsed = dateparser.parse(date_match.group(1), dayfirst=True)
                event_date = parsed.strftime("%Y-%m-%d")
                title = title.replace(date_match.group(0), "")
            except:
                pass

        ev = db.Event(title=title.strip(), date=event_date, family_member=member, source="whatsapp")
        eid = db.add_event(ev)
        return {"status": "created", "id": eid, "type": "event"}

    elif body.startswith("\uD83D\uDCB0") or body.startswith("/cash") or body.startswith("cash "):
        parts = body.replace("\uD83D\uDCB0", "").replace("/cash", "cash").strip()
        amount_match = re.search(r'\$?(\d+(?:\.\d{2})?)', parts)
        amount = float(amount_match.group(1)) if amount_match else 0.0
        title = parts
        if amount_match:
            title = parts.replace(f"${amount:.0f}" if amount == int(amount) else f"${amount:.2f}", "").replace(str(amount), "").strip()
        ci = db.CashItem(title=title.strip(), amount=amount, source="whatsapp")
        cid, _ = db.add_cash_item(ci)
        if cid == -1:
            return {"status": "ignored", "reason": "title is on the ignore list"}
        return {"status": "created", "id": cid, "type": "cash"}

    return {"status": "ignored", "reason": "Unknown format. Use 📅 for events, 💰 for cash items."}