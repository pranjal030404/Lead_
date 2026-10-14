"""Social profile registry.

Profiles come from links the business publishes on its own website (found by the
website check) or are entered by a user. Follower counts, post frequency and last
activity need each platform's official API; none is configured, so those columns
stay NULL rather than being scraped or guessed.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .db import current_ws, execute, query, query_one, utcnow
from .lineage import SOURCE_CONFIDENCE

PLATFORM_HOSTS = {
    "instagram": ("instagram.com",), "facebook": ("facebook.com", "fb.com"),
    "linkedin": ("linkedin.com",), "youtube": ("youtube.com", "youtu.be"),
    "x": ("twitter.com", "x.com"),
}
LEAD_URL_COLUMNS = {"instagram": "instagram_url", "facebook": "facebook_url",
                    "linkedin": "linkedin_url"}
_RESERVED = {"p", "reel", "reels", "explore", "share", "sharer", "plugins", "login",
             "intent", "hashtag", "watch", "tr", "pages", "groups", "events", "home"}


def parse(url: str | None) -> dict | None:
    """-> {platform, username, url} for a recognised profile URL, else None."""
    if not url:
        return None
    raw = url.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    for platform, hosts in PLATFORM_HOSTS.items():
        if host in hosts or any(host.endswith("." + h) for h in hosts):
            parts = [p for p in parsed.path.split("/") if p]
            if platform == "linkedin" and len(parts) >= 2 and parts[0] in ("company", "in"):
                username = parts[1]
            elif platform == "youtube" and parts and parts[0] in ("c", "channel", "user") \
                    and len(parts) > 1:
                username = parts[1]
            elif platform == "youtube" and parts and parts[0].startswith("@"):
                username = parts[0]
            elif parts and parts[0].lower() not in _RESERVED:
                username = parts[0]
            else:
                return None
            username = re.sub(r"[^\w.\-@]", "", username)
            if not username:
                return None
            clean = f"https://{parsed.hostname}{parsed.path}".rstrip("/")
            return {"platform": platform, "username": username.lstrip("@"), "url": clean}
    return None


def register(lead_id: int, url: str, source: str, source_type: str,
             match_confidence: float | None = None) -> dict | None:
    info = parse(url)
    if not info:
        return None
    conf = SOURCE_CONFIDENCE.get(source, 0.5) if match_confidence is None else match_confidence
    now = utcnow()
    execute(
        """INSERT INTO social_profiles
           (workspace_id, lead_id, platform, url, username, match_confidence, source,
            source_type, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON DUPLICATE KEY UPDATE
             url = IF(VALUES(match_confidence) >= match_confidence, VALUES(url), url),
             username = IF(VALUES(match_confidence) >= match_confidence,
                           VALUES(username), username),
             source = IF(VALUES(match_confidence) >= match_confidence, VALUES(source), source),
             source_type = IF(VALUES(match_confidence) >= match_confidence,
                              VALUES(source_type), source_type),
             match_confidence = GREATEST(match_confidence, VALUES(match_confidence)),
             updated_at = VALUES(updated_at)""",
        (current_ws(), lead_id, info["platform"], info["url"], info["username"], conf,
         source, source_type, now, now),
    )
    return query_one("SELECT * FROM social_profiles WHERE workspace_id = ? AND lead_id = ? "
                     "AND platform = ?", (current_ws(), lead_id, info["platform"]))


def register_lead_columns(lead_id: int, fields: dict, source: str, source_type: str) -> None:
    for column in LEAD_URL_COLUMNS.values():
        if fields.get(column):
            register(lead_id, fields[column], source, source_type)


def list_for_lead(lead_id: int) -> list[dict]:
    return query("SELECT * FROM social_profiles WHERE workspace_id = ? AND lead_id = ? "
                 "ORDER BY platform", (current_ws(), lead_id))
