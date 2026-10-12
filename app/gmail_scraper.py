"""Gmail scraper — reads forwarded school emails, extracts events and cash items via LLM."""
import os
import json
from datetime import datetime
from typing import List, Tuple

# Google API
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# OpenAI client for OpenRouter
from openai import OpenAI

from . import database as db
from . import members


def get_gmail_service():
    """Authenticate and return Gmail API service."""
    token_path = os.environ.get("GOOGLE_TOKEN", "/app/google_token.json")
    if not os.path.exists(token_path):
        print("WARN: No Google token found at", token_path)
        return None
    with open(token_path) as f:
        creds = Credentials.from_authorized_user_info(json.load(f))
    return build("gmail", "v1", credentials=creds)


def get_openrouter_client():
    """Return OpenAI-compatible client pointed at OpenRouter."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        # Try reading from .env or mounted file
        try:
            with open("/app/.env") as f:
                for line in f:
                    if line.startswith("OPENROUTER_API_KEY"):
                        api_key = line.strip().split("=", 1)[1].strip('"').strip("'")
                        break
        except FileNotFoundError:
            pass
    if not api_key:
        print("WARN: No OpenRouter API key found")
        return None
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        default_headers={"HTTP-Referer": "https://family-board.local"},
    )


def build_system_prompt() -> str:
    """Build the LLM system prompt from the current member configuration."""
    alias_block = members.alias_hints()
    if alias_block:
        alias_block = (
            "Identify the family member from the school, grade, class/teacher or activity:\n"
            f"{alias_block}\n"
            "If a school email does not clearly state the student, use your best judgement\n"
            'from the class/teacher name, otherwise use "all".\n'
        )
    return f"""You are a family calendar assistant. Parse school/activity emails and extract structured data.

Return a JSON array of items found in the email. Each item is either:
{{"type":"event", "title":"...", "date":"YYYY-MM-DD", "time":"HH:MM or null", "end_time":"HH:MM or null", "family_member":"which family member or all", "category":"school|sport|medical|other", "description":"..."}}
{{"type":"cash", "title":"...", "amount":0.0, "due_date":"YYYY-MM-DD or null", "category":"school|field_trip|lunch|other", "family_member":"which family member or all", "notes":"..."}}
{{"type":"ignore", "reason":"why this email has no calendar items"}}

If the email has NO events or cash items, return [{{"type":"ignore","reason":"..."}}].
Be thorough — extract dates, times, amounts, and which family member it's for.

SchoolCash Online emails come in two kinds:
- "Item payment reminder": items NOT yet paid. If no amount is stated, use "amount":0.0 (the
  payer picks the amount in the app). Set "due_date" to the payment deadline when shown.
- "RECEIPT": confirms an item was paid. Set "amount" to the amount paid, and use the SAME
  short item title as the reminder (e.g. "Jamaican Patty Day") so both become one entry.
Keep week-specific titles for recurring programs (e.g. "Week of Oct 5 - Oct 9 Lunch Program")
so different weeks stay distinct.

Family members (use these exact lowercase keys for "family_member"):
{members.family_context()}

{alias_block}Use "all" only when the item genuinely concerns the whole family."""


def parse_email_with_llm(subject: str, body_text: str) -> List[dict]:
    """Send email content to LLM and get structured items."""
    client = get_openrouter_client()
    if not client:
        return []

    prompt = f"""Subject: {subject}

Body:
{body_text[:8000]}  # truncate to avoid token limits
"""

    try:
        resp = client.chat.completions.create(
            model="google/gemini-2.5-flash-lite",
            messages=[
                {"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content.strip()
        # Extract JSON array
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        data = json.loads(content)
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        if isinstance(data, list):
            return data
        return [data]
    except Exception as e:
        print(f"LLM parse error: {e}")
        return []


def scrape_emails(max_emails: int = 20) -> Tuple[int, int]:
    """Scan Gmail inbox for unread school-ish emails, parse them, add to DB.
    Returns (#events_created, #cash_items_created)."""
    service = get_gmail_service()
    if not service:
        return 0, 0

    events_created = 0
    cash_created = 0

    try:
        # This inbox is dedicated to forwarded school/activity email, so read
        # all recent unread mail and let the LLM ignore anything that isn't an
        # event or payment. Forwarded mail can arrive either manually (From =
        # the forwarder, Subject "Fwd:") or via a Gmail filter (From = the
        # original sender), so there is no reliable "forwarded" search term.
        results = service.users().messages().list(
            userId="me",
            maxResults=max_emails,
            q="is:unread newer_than:7d"
        ).execute()

        messages = results.get("messages", [])
        print(f"Found {len(messages)} unread school-like emails")

        for msg in messages:
            msg_id = msg["id"]
            if db.is_email_processed(msg_id):
                continue

            # Get full message
            full = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
            headers = {h["name"]: h["value"] for h in full["payload"]["headers"]}
            subject = headers.get("Subject", "(no subject)")
            from_addr = headers.get("From", "unknown")

            # Extract body text
            body = _extract_body(full["payload"])

            # Parse with LLM
            items = parse_email_with_llm(subject, body)

            if not items:
                db.mark_email_processed(msg_id, subject, from_addr, body, False)
                continue

            has_items = False
            for item in items:
                if item.get("type") == "event":
                    ev = db.Event(
                        title=item["title"],
                        description=item.get("description", subject),
                        date=item.get("date", ""),
                        time=item.get("time"),
                        end_time=item.get("end_time"),
                        family_member=item.get("family_member", "all"),
                        category=item.get("category", "school"),
                        source="email",
                    )
                    if ev.date:
                        db.add_event(ev)
                        events_created += 1
                        has_items = True

                elif item.get("type") == "cash":
                    ci = db.CashItem(
                        title=item["title"],
                        amount=item.get("amount", 0.0),
                        due_date=item.get("due_date"),
                        family_member=item.get("family_member", "all"),
                        category=item.get("category", "school"),
                        source="email",
                        notes=item.get("notes", ""),
                    )
                    cid, created = db.add_cash_item(ci)
                    if cid != -1:  # -1 means suppressed by an ignore rule
                        has_items = True
                        if created:
                            cash_created += 1

            db.mark_email_processed(msg_id, subject, from_addr, body, has_items)

            # Mark as read
            try:
                service.users().messages().modify(
                    userId="me", id=msg_id,
                    body={"removeLabelIds": ["UNREAD"]}
                ).execute()
            except Exception:
                pass

    except Exception as e:
        print(f"Gmail scrape error: {e}")

    return events_created, cash_created


def _extract_body(payload: dict) -> str:
    """Recursively extract text from MIME payload."""
    if "parts" in payload:
        for part in payload["parts"]:
            result = _extract_body(part)
            if result:
                return result
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain" and "body" in payload and "data" in payload["body"]:
        import base64
        try:
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        except Exception:
            return ""
    return ""