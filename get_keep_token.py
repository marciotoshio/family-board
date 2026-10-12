"""Generate a Google Keep master token for the shared family account.

Google Keep has no public API, so the board reads it with the unofficial
`gkeepapi`, which needs a *master token* (it has full account access — treat it
like a password).

IMPORTANT: Google has broken the password / App-Password login that the old
`keep.login()` flow used; it now usually fails with `BadAuthentication`, even
with a correct App Password (spaces and all). Use the browser-assisted token
exchange below instead.

Usage:
    pip install gkeepapi gpsoauth
    python get_keep_token.py

Then put the printed values in the homelab env file
(/mnt/homelab-data/environments/family-board.env):

    KEEP_EMAIL=...
    KEEP_MASTER_TOKEN=...
"""
import sys

try:
    import gpsoauth
except ImportError:
    sys.exit("Install dependencies first:  pip install gkeepapi gpsoauth")

DEFAULT_ANDROID_ID = "0123456789abcdef"


def main():
    email = input("Shared Google account email: ").strip()
    if not email:
        sys.exit("No email given.")

    print(
        "\nBrowser-assisted flow:\n"
        "  1. In a browser, sign in to the SHARED account, then open:\n"
        "       https://accounts.google.com/EmbeddedSetup\n"
        "  2. Open DevTools -> Application -> Cookies -> https://accounts.google.com\n"
        "  3. Copy the value of the 'oauth_token' cookie (it looks like\n"
        "     'oauth2_4/...'; it is NOT the account password).\n"
    )
    oauth_token = input("oauth_token cookie value: ").strip()
    if not oauth_token:
        sys.exit("No oauth_token given.")

    android_id = input(f"Android ID [{DEFAULT_ANDROID_ID}]: ").strip() or DEFAULT_ANDROID_ID

    resp = gpsoauth.exchange_token(email, oauth_token, android_id)
    token = resp.get("Token")
    if not token:
        sys.exit(f"Failed to obtain master token: {resp}")

    print("\nSuccess. Add these to the homelab env file:\n")
    print(f"KEEP_EMAIL={email}")
    print(f"KEEP_MASTER_TOKEN={token}")


if __name__ == "__main__":
    main()
