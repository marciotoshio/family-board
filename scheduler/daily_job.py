"""Daily scheduler — runs inside the container via cron or APScheduler.
Triggers email scraping and Google Calendar sync."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import database as db
from app import gmail_scraper
from app import calendar_sync
from datetime import datetime

def run():
    print(f"[{datetime.now()}] Family Board — Daily Job Starting")
    db.init_db()

    # 1. Scrape Gmail for forwarded school emails
    try:
        ev, ci = gmail_scraper.scrape_emails()
        print(f"  ✓ Scraped emails: {ev} events, {ci} cash items")
    except Exception as e:
        print(f"  ✗ Scrape error: {e}")

    # 2. Sync local events to Google Calendar
    try:
        synced = calendar_sync.sync_events()
        print(f"  ✓ Synced {synced} events to Google Calendar")
    except Exception as e:
        print(f"  ✗ Sync error: {e}")

    # 3. Pull remote events from Google Calendar
    try:
        fetched = calendar_sync.fetch_remote_events()
        print(f"  ✓ Fetched {fetched} events from Google Calendar")
    except Exception as e:
        print(f"  ✗ Fetch error: {e}")

    print(f"[{datetime.now()}] Family Board — Daily Job Complete")

if __name__ == "__main__":
    run()