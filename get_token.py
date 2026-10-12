"""One-time OAuth consent helper for Family Board.

Run this ONCE, on a machine with a browser, to turn a Google OAuth client
JSON into the `google_token.json` (authorized_user format) that the app
loads via GOOGLE_TOKEN / credentials in app/gmail_scraper.py and
app/calendar_sync.py.

Usage:
    python get_token.py <client_secret.json> [port]

- Desktop app client:  port can be omitted (a random loopback port is used).
- Web application client: pass a port you registered as an Authorized
  redirect URI, e.g. 8081 -> register http://localhost:8081/ in Google Cloud.

Requires: pip install google-auth-oauthlib
"""
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",     # read + mark processed mail read
    "https://www.googleapis.com/auth/calendar",         # sync inserts/updates
]

OUTPUT = os.environ.get("GOOGLE_TOKEN", "google_token.json")


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        return 1

    client_file = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) == 3 else 0

    flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
    creds = flow.run_local_server(port=port)

    with open(OUTPUT, "w") as f:
        f.write(creds.to_json())

    print(f"Wrote {OUTPUT} (authorized_user format)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
