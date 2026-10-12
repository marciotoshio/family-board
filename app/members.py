"""Configurable family roster.

The roster is loaded from (in priority order):

1. A JSON file at ``$MEMBERS_FILE`` (default ``/app/members.json`` — the file is
   bind-mounted from the homelab shared volume, see AGENTS.md)
2. The ``$FAMILY_MEMBERS`` environment variable (same JSON, inline)
3. The built-in defaults below

The JSON may be either ``{"app_title": "...", "members": [...]}`` or a bare
list of members. Each member looks like::

    {
      "key": "jamie",                # stable id stored in the DB (lowercase)
      "label": "Jamie",              # shown in the UI
      "emoji": "👦",
      "color": "#6366f1",            # event chip colour
      "context": "son, 3rd grade at Maplewood Elementary",  # hint for the LLM
      "aliases": ["Maplewood Elementary", "Grade 3"]        # attribution hints
    }

The synthetic "all" member is always present and listed first.
"""
import os
import json

ALL_KEY = "all"

DEFAULT_APP_TITLE = "Family Board"

DEFAULT_ALL = {
    "key": ALL_KEY,
    "label": "All family",
    "emoji": "\U0001F468\u200D\U0001F469\u200D\U0001F467\u200D\U0001F466",
    "color": "#6b7280",
    "context": "items that concern the whole family",
    "aliases": [],
}

DEFAULT_MEMBERS = [
    DEFAULT_ALL,
    {
        "key": "alex",
        "label": "Alex",
        "emoji": "\U0001F469",
        "color": "#ec4899",
        "context": "mother (adult)",
        "aliases": [],
    },
    {
        "key": "sam",
        "label": "Sam",
        "emoji": "\U0001F468",
        "color": "#3b82f6",
        "context": "father (adult)",
        "aliases": [],
    },
    {
        "key": "jamie",
        "label": "Jamie",
        "emoji": "\U0001F466",
        "color": "#6366f1",
        "context": "son, 3rd grade at Maplewood Elementary",
        "aliases": ["Maplewood Elementary", "Grade 3"],
    },
    {
        "key": "taylor",
        "label": "Taylor",
        "emoji": "\U0001F467",
        "color": "#14b8a6",
        "context": "daughter, 7th grade at Northview Middle School",
        "aliases": ["Northview Middle School", "Grade 7", "Soccer"],
    },
    {
        "key": "riley",
        "label": "Riley",
        "emoji": "\U0001F466",
        "color": "#f59e0b",
        "context": "son, 10th grade at Westbrook High School",
        "aliases": ["Westbrook High School", "Grade 10"],
    },
    {
        "key": "casey",
        "label": "Casey",
        "emoji": "\U0001F9D1",
        "color": "#a855f7",
        "context": "daughter, 12th grade at Westbrook High School",
        "aliases": ["Westbrook High School", "Grade 12", "Gymnastics"],
    },
]


def _config_path() -> str:
    return os.environ.get("MEMBERS_FILE", "/app/members.json")


def _normalize_member(raw: dict) -> dict:
    key = str(raw.get("key", "")).strip().lower()
    return {
        "key": key,
        "label": str(raw.get("label") or key).strip(),
        "emoji": str(raw.get("emoji") or "").strip(),
        "color": str(raw.get("color") or "").strip(),
        "context": str(raw.get("context") or "").strip(),
        "aliases": [str(a).strip() for a in (raw.get("aliases") or []) if str(a).strip()],
    }


def _load_config() -> dict:
    """Return {"app_title": str, "members": [dict, ...]}."""
    raw = None

    path = _config_path()
    if path and os.path.exists(path):
        try:
            with open(path) as f:
                raw = json.load(f)
        except Exception as e:
            print(f"WARN: could not read members file {path}: {e}")

    if raw is None:
        env = os.environ.get("FAMILY_MEMBERS")
        if env:
            try:
                raw = json.loads(env)
            except Exception as e:
                print(f"WARN: invalid FAMILY_MEMBERS JSON: {e}")

    if raw is None:
        return {"app_title": DEFAULT_APP_TITLE, "members": list(DEFAULT_MEMBERS)}

    if isinstance(raw, dict):
        title = str(raw.get("app_title") or DEFAULT_APP_TITLE).strip()
        raw_members = raw.get("members")
    else:
        title = DEFAULT_APP_TITLE
        raw_members = raw

    if not isinstance(raw_members, list) or not raw_members:
        print("WARN: no members in config; using built-in defaults")
        return {"app_title": title or DEFAULT_APP_TITLE, "members": list(DEFAULT_MEMBERS)}

    members = [_normalize_member(m) for m in raw_members if isinstance(m, dict)]
    members = [m for m in members if m["key"]]
    if not members:
        print("WARN: no valid members in config; using built-in defaults")
        return {"app_title": title or DEFAULT_APP_TITLE, "members": list(DEFAULT_MEMBERS)}

    if ALL_KEY not in {m["key"] for m in members}:
        members.insert(0, dict(DEFAULT_ALL))

    # "all" is always shown first; everything else keeps its configured order.
    members.sort(key=lambda m: 0 if m["key"] == ALL_KEY else 1)
    return {"app_title": title or DEFAULT_APP_TITLE, "members": members}


def get_members() -> list:
    return _load_config()["members"]


def get_app_title() -> str:
    return _load_config()["app_title"]


def all_key() -> str:
    return ALL_KEY


def match_member(text: str):
    """Return the key of the first member mentioned in ``text`` (or None)."""
    low = (text or "").lower()
    for m in get_members():
        if m["key"] == ALL_KEY:
            continue
        for needle in [m["key"], m["label"], *m["aliases"]]:
            if needle and needle.lower() in low:
                return m["key"]
    return None


def family_context() -> str:
    """Member roster as a prompt block for the LLM."""
    lines = []
    for m in get_members():
        ctx = m["context"] or ("items that concern the whole family" if m["key"] == ALL_KEY else "")
        lines.append(f"- {m['key']}: {ctx}" if ctx else f"- {m['key']}")
    return "\n".join(lines)


def alias_hints() -> str:
    """School/grade/activity → member mapping for the LLM prompt."""
    lines = []
    for m in get_members():
        if m["key"] == ALL_KEY or not m["aliases"]:
            continue
        lines.append(f"- {', '.join(m['aliases'])} \u2192 {m['key']}")
    return "\n".join(lines)


def public_config() -> dict:
    """Minimal, frontend-safe view of the roster."""
    return {
        "app_title": get_app_title(),
        "members": [
            {"key": m["key"], "label": m["label"], "emoji": m["emoji"], "color": m["color"]}
            for m in get_members()
        ],
    }
