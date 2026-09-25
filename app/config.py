"""Configuration loaded from the environment / .env file."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


class Settings:
    # paths
    base_dir = BASE_DIR
    data_dir = BASE_DIR / "data"
    backup_dir = BASE_DIR / "backups"
    db_path = BASE_DIR / "data" / "leadgen.db"  # legacy SQLite path, used only by the migration script
    static_dir = Path(__file__).resolve().parent / "static"

    # MySQL connection (the primary store)
    mysql_host = _str("MYSQL_HOST", "127.0.0.1")
    mysql_port = _int("MYSQL_PORT", 3306)
    mysql_user = _str("MYSQL_USER", "leadgen")
    mysql_password = _str("MYSQL_PASSWORD")
    mysql_database = _str("MYSQL_DATABASE", "leadgen")
    mysql_charset = _str("MYSQL_CHARSET", "utf8mb4")
    mysqldump_path = _str("MYSQLDUMP_PATH", "mysqldump")
    # seconds to wait for a connection to the DB server - set high enough for a
    # container that is still warming up when the app starts
    mysql_connect_timeout = _float("MYSQL_CONNECT_TIMEOUT", 10)

    # concurrency
    enrich_workers = _int("ENRICH_WORKERS", 8)
    http_pool_size = _int("HTTP_POOL_SIZE", 32)

    # Behind a reverse proxy, request.client.host is the *proxy*. Turn this on
    # there so rate limits key on the real visitor instead of lumping everyone
    # into one bucket. Leave it off when the app is directly exposed: a client
    # can put anything in X-Forwarded-For, so trusting it unproxied hands out a
    # free way to dodge the limiter.
    trust_proxy = _bool("TRUST_PROXY", False)

    # request limits (per client IP)
    api_rate_limit = _int("API_RATE_LIMIT", 600)
    api_rate_window = _int("API_RATE_WINDOW", 60)
    login_rate_limit = _int("LOGIN_RATE_LIMIT", 8)
    login_rate_window = _int("LOGIN_RATE_WINDOW", 300)
    max_page_size = _int("MAX_PAGE_SIZE", 500)

    # auth
    admin_user = _str("ADMIN_USER", "admin")
    admin_password = _str("ADMIN_PASSWORD")
    secret_key = _str("SECRET_KEY")
    session_hours = _int("SESSION_HOURS", 168)
    cookie_secure = _bool("COOKIE_SECURE", False)

    # provider
    provider = _str("PROVIDER", "osm").lower()
    google_api_key = _str("GOOGLE_API_KEY")

    # location: "osm" (free Nominatim) or "google" (bills per call, needs a key)
    geocoder = _str("GEOCODER", "osm").lower()
    nearby_radius_m = _int("NEARBY_RADIUS_M", 3000)
    max_nearby_radius_m = _int("MAX_NEARBY_RADIUS_M", 50000)

    # quota
    max_google_calls_per_day = _int("MAX_GOOGLE_CALLS_PER_DAY", 500)
    max_results_per_search = _int("MAX_RESULTS_PER_SEARCH", 60)
    quota_warn_pct = _int("QUOTA_WARN_PCT", 80)
    enterprise_fetch_min_score = _int("ENTERPRISE_FETCH_MIN_SCORE", 5)

    # search-result cache: an identical manual query re-uses the leads a previous
    # search already pulled, instead of calling the provider again. Gated on the
    # search content (niche/city/area/country/provider/point) so "changes
    # anything" = cache miss. Automated/target runs always scrape fresh, so the
    # cache is refreshed by the daily sweep instead of going stale forever.
    search_cache_enabled = _bool("SEARCH_CACHE_ENABLED", True)
    search_cache_ttl_days = _int("SEARCH_CACHE_TTL_DAYS", 7)

    # enrichment
    http_timeout = _int("HTTP_TIMEOUT", 8)
    website_cache_days = _int("WEBSITE_CACHE_DAYS", 30)
    hunter_api_key = _str("HUNTER_API_KEY")
    max_hunter_calls_per_month = _int("MAX_HUNTER_CALLS_PER_MONTH", 25)

    # outreach
    dry_run = _bool("DRY_RUN", True)
    resend_api_key = _str("RESEND_API_KEY")
    mail_from = _str("MAIL_FROM")
    mail_from_name = _str("MAIL_FROM_NAME")
    max_emails_per_hour = _int("MAX_EMAILS_PER_HOUR", 20)
    unsubscribe_mailto = _str("UNSUBSCRIBE_MAILTO")

    # operator alerts (Part 7.6)
    alerts_enabled = _bool("ALERTS_ENABLED", True)
    alert_email = _str("ALERT_EMAIL")
    # Blueprint 7.6 says "scores 9-10", but 9 is unreachable in practice: the
    # only rule that could lift an ideal prospect past 8 is the social bonus,
    # and that needs socials scraped from a website the lead doesn't have. A
    # threshold of 9 ships an alert that never fires. 8 is the top of the real
    # range and the HOT boundary. See the README deviations section.
    hot_alert_min_score = _int("HOT_ALERT_MIN_SCORE", 8)

    # automation
    automation_enabled = _bool("AUTOMATION_ENABLED", False)
    discovery_time = _str("DISCOVERY_TIME", "06:00")
    enrichment_time = _str("ENRICHMENT_TIME", "06:30")
    scoring_time = _str("SCORING_TIME", "07:00")
    followup_time = _str("FOLLOWUP_TIME", "07:30")
    digest_time = _str("DIGEST_TIME", "08:00")
    backup_time = _str("BACKUP_TIME", "02:00")
    targets_per_run = _int("TARGETS_PER_RUN", 3)
    backup_keep_days = _int("BACKUP_KEEP_DAYS", 30)

    # off-server backup sync (Part 7.4) - blank disables it
    backup_remote = _str("BACKUP_REMOTE")
    rclone_path = _str("RCLONE_PATH", "rclone")
    backup_sync_timeout = _int("BACKUP_SYNC_TIMEOUT", 300)

    # server
    host = _str("HOST", "127.0.0.1")
    port = _int("PORT", 8000)

    # filled in at startup when not configured
    generated_password: str | None = None
    generated_secret: bool = False

    def ensure_runtime_secrets(self) -> None:
        """Generate anything the operator left blank, so first run just works."""
        if not self.secret_key:
            # Regenerated every boot, which silently signs everyone out on
            # restart - the startup banner calls this out.
            self.secret_key = secrets.token_hex(32)
            self.generated_secret = True
        if not self.admin_password:
            self.generated_password = secrets.token_urlsafe(12)
            self.admin_password = self.generated_password


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.backup_dir.mkdir(parents=True, exist_ok=True)


def parse_hhmm(value: str, fallback: tuple[int, int]) -> tuple[int, int]:
    try:
        hh, mm = value.split(":")
        return int(hh), int(mm)
    except Exception:
        return fallback
