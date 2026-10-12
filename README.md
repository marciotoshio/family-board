# Family Board

Smart family calendar for Fire HD 8 kiosk. Syncs with Google Calendar, scrapes school emails via Gmail API, and accepts new items via WhatsApp/web form.

## Quick Start

### 1. Prerequisites

- Docker + Docker Compose (or Podman with compose)
- Google OAuth token for Gmail + Calendar API (already set up — use existing `google_token.json`)
- OpenRouter API key for AI email parsing

### 2. Files needed

Place these in the project root:
```
family-board/
├── google_token.json        # From Hermes Google Workspace setup
├── google_client_secret.json # From Google Cloud Console
├── members.json             # Your family roster (see §3)
└── .env                     # OPENROUTER_API_KEY=sk-or-...
```

### 3. Configure your family

The family roster is fully configurable — no code changes needed. Create a
`members.json` file (start from `members.example.json`, then change the
placeholder names, schools and colours):

```bash
cp members.example.json members.json
```

```json
{
  "app_title": "Family Board",
  "members": [
    {
      "key": "jamie",
      "label": "Jamie",
      "emoji": "👦",
      "color": "#6366f1",
      "context": "son, 3rd grade at Maplewood Elementary",
      "aliases": ["Maplewood Elementary", "Grade 3"]
    },
    {
      "key": "taylor",
      "label": "Taylor",
      "emoji": "👧",
      "color": "#14b8a6",
      "context": "daughter, 7th grade at Northview Middle School",
      "aliases": ["Northview Middle School", "Grade 7", "Soccer"]
    }
  ]
}
```

- `key` is the stable lowercase id stored in the database — change it rarely.
- `label`, `emoji` and `color` control the filter tabs, dropdowns, and event-chip colours.
- `context` tells the AI parser who this person is.
- `aliases` teaches the AI (and the WhatsApp bot) to attribute items, e.g.
  schools, grades, or activities like "Gymnastics".

The file is mounted into the container at `/app/members.json`, so editing it
and restarting the service is all that's needed. If no file is present, a
built-in generic roster is used. You can also pass the same JSON inline via
the `FAMILY_MEMBERS` env var (and the file path via `MEMBERS_FILE`).

> **Note:** `members.json` is git-ignored — it holds your real family data and
> should never be committed to the repo. Only the placeholder
> `members.example.json` is tracked.

### 4. Build & Run

```bash
cd family-board
docker compose up -d --build
```

The app runs on `http://localhost:8080`.

### 5. Fire Tablet Setup

Fire OS has no Google Play and Fully Kiosk is no longer on the Amazon App
Store, so use **FreeKiosk** (free, open source) or Silk.

> **Use the server IP, not the hostname.** Fire OS does not resolve mDNS
> `.local` names, so `homelab.local` will NOT work on the tablet. Use
> `http://<your-server-ip>:8084` (the port published in `docker-compose.yml`;
> 8084 in the homelab deployment, 8080 for a local dev run).

**Option A — FreeKiosk (recommended):**
1. On the tablet, install **Downloader** (by AFTVnews) from the Amazon App Store.
2. Settings → Security & Privacy → Apps from Unknown Sources → allow Downloader.
3. In Downloader, enter the APK URL from https://freekiosk.app and install.
4. Open FreeKiosk → set URL to `http://<your-server-ip>:8084`.
5. Enable **Kiosk Mode**, **Auto-start on boot**, and **Keep screen on**.

**Option B — Silk Browser (no sideloading):**
1. Open Silk Browser
2. Navigate to `http://<your-server-ip>:8084`
3. Bookmark → "Add to Favorites"
4. From home screen, tap Favorites → Family Board
5. To lock it down: Settings → Apps → App Mode → select Silk Browser

**Tip:** Set a DHCP reservation (or static IP) for the server so its address
never changes, otherwise the tablet's Start URL will go stale.

### 6. Google Calendar Setup

The app uses your existing Hermes Google token. Calendar sync is bi-directional:
- Events you add via Family Board → push to Google Calendar
- Events on Google Calendar → pull into Family Board

Set your calendar ID:
```yaml
# In docker-compose.yml, add under environment:
# GCAL_ID: primary  # or your specific calendar ID
```

### 7. WhatsApp Input

Messages sent to the Hermes WhatsApp number can be forwarded to:
```
POST http://localhost:8080/api/whatsapp
{"from_number": "...", "body": "📅 Jamie soccer Wed 4pm"}
```

Format:
- `📅` or `/add` → calendar event  
- `💰` or `/cash` → cash/payment item

### 8. Sidebar (weather, message, shopping list)

The right sidebar shows a live clock, local weather, a family message, and the
shared shopping list.

**Weather** uses Open-Meteo (no API key). Configure the location via env:
```
WEATHER_LAT=43.5890
WEATHER_LON=-79.6441
WEATHER_PLACE=Mississauga
SIDEBAR_REFRESH_MINUTES=10
```

**Message & shopping list** are read from Google Keep via the unofficial
`gkeepapi` library (Keep has no public API). The board logs into the shared
family account and reads two notes:
- `Shopping list` — Assistant/Nest "add to shopping list" and Gemini write here
- `Family Board` — its text is shown as the family message

Override the note titles with `KEEP_SHOPPING_NOTE` / `KEEP_MESSAGE_NOTE`.

Generate a master token **once** on your workstation:
```bash
pip install gkeepapi
python get_keep_token.py
```
Then add the printed values to the env file (an App Password is required if the
account has 2FA):
```
KEEP_EMAIL=your-shared-account@gmail.com
KEEP_MASTER_TOKEN=...
```

The board only reads Keep, so all adding/checking-off happens in the Google
apps (Nest, Gemini, Keep on phones) — input can come from anywhere.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Kiosk web UI |
| `/api/events` | GET | List events (query: start, end, member) |
| `/api/events` | POST | Create event |
| `/api/cash` | GET | List cash items (?include_paid=true) |
| `/api/cash` | POST | Create cash item |
| `/api/cash/:id/pay` | POST | Mark paid |
| `/api/cash/:id/ignore` | POST | Delete + suppress future items with the same title |
| `/api/cash/:id` | DELETE | Delete a cash item (no suppression) |
| `/api/cash/ignored` | GET | List ignore rules |
| `/api/cash/ignored/:id` | DELETE | Remove an ignore rule |
| `/api/overview` | GET | Kiosk dashboard data (includes weather/shopping/message) |
| `/api/config` | GET | App title + family roster (drives the kiosk UI) |
| `/api/scrape` | POST | Trigger email scraping |
| `/api/sync` | POST | Trigger Google Calendar sync |
| `/api/whatsapp` | POST | WhatsApp webhook |

## Architecture

```
Browser (Fire tablet)  ←→  FastAPI (port 8080)
                              │
                    ┌─────────┴─────────┐
                    │                   │
              Gmail API          Google Calendar
              (school emails)    (family sync)
                    │
              OpenRouter AI
              (parse → events/cash)
```

## Background Schedule

The app runs a sync job (every `SYNC_INTERVAL_HOURS`, default 2h) that:
1. Scans Gmail for unread forwarded school emails
2. Uses AI to extract events and payment items
3. Syncs new events to Google Calendar
4. Pulls remote Google Calendar events into local DB

A second sidebar job (every `SIDEBAR_REFRESH_MINUTES`, default 10m) refreshes
the weather and Google Keep caches. Both run via APScheduler in the container.