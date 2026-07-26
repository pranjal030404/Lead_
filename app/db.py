"""SQLite access layer + schema.

One connection **per thread**, reused for that thread's lifetime. A SQLite
connection object is not safe to share across threads, but opening a fresh one
per query is what actually costs: measured at 3,000 trivial SELECTs, the
open + PRAGMA round trip was ~9ms of the ~9ms. Keeping the handle on
thread-local storage removes that entirely, and request threads, scheduler
threads and the enrichment pool are all bounded and reused, so the number of
live connections stays small.

Raw SQL, no ORM - keeps the dependency list tiny and the queries obvious.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import settings

SCHEMA_VERSION = 1

PIPELINE_STAGES = [
    ("NEW", 1, "#6b7280", 0),
    ("CONTACTED", 2, "#3b82f6", 3),
    ("REPLIED", 3, "#8b5cf6", 2),
    ("MEETING_SET", 4, "#06b6d4", 2),
    ("PROPOSAL_SENT", 5, "#f59e0b", 3),
    ("NEGOTIATION", 6, "#f97316", 3),
    ("WON", 7, "#22c55e", 0),
    ("LOST", 8, "#ef4444", 0),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name           TEXT NOT NULL,
    owner_name              TEXT,
    phone                   TEXT,
    phone_valid             INTEGER DEFAULT 0,
    email                   TEXT,
    email_valid             INTEGER DEFAULT 0,
    whatsapp                TEXT,
    website_url             TEXT,
    address                 TEXT,
    city                    TEXT,
    area                    TEXT,
    pincode                 TEXT,
    state                   TEXT,
    country                 TEXT,
    lat                     REAL,
    lng                     REAL,
    place_id                TEXT UNIQUE,
    google_maps_url         TEXT,
    google_rating           REAL,
    google_reviews          INTEGER,
    instagram_url           TEXT,
    facebook_url            TEXT,
    linkedin_url            TEXT,
    has_website             INTEGER,
    website_score           INTEGER,
    website_issues          TEXT,
    website_platform        TEXT,
    website_ssl             INTEGER,
    website_mobile_friendly INTEGER,
    website_speed_ms        INTEGER,
    website_last_updated    TEXT,
    website_checked_at      TEXT,
    lead_score              INTEGER DEFAULT 0,
    lead_priority           TEXT DEFAULT 'COLD',
    data_quality_score      INTEGER DEFAULT 0,
    score_reasons           TEXT,
    status                  TEXT DEFAULT 'NEW',
    source                  TEXT,
    niche                   TEXT,
    tags                    TEXT,
    notes                   TEXT,
    estimated_budget        TEXT,
    business_size           TEXT,
    business_status         TEXT DEFAULT 'OPERATIONAL',
    enrichment_status       TEXT DEFAULT 'pending',
    enrichment_error        TEXT,
    first_contacted_date    TEXT,
    last_contacted_date     TEXT,
    follow_up_date          TEXT,
    total_touchpoints       INTEGER DEFAULT 0,
    contact_method          TEXT,
    search_id               INTEGER,
    found_date              TEXT,
    created_at              TEXT,
    updated_at              TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_status    ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_priority  ON leads(lead_priority);
CREATE INDEX IF NOT EXISTS idx_leads_city      ON leads(city);
CREATE INDEX IF NOT EXISTS idx_leads_niche     ON leads(niche);
CREATE INDEX IF NOT EXISTS idx_leads_followup  ON leads(follow_up_date);
CREATE INDEX IF NOT EXISTS idx_leads_namecity  ON leads(business_name, city);
CREATE INDEX IF NOT EXISTS idx_leads_phone     ON leads(phone);
CREATE INDEX IF NOT EXISTS idx_leads_enrich    ON leads(enrichment_status);

CREATE TABLE IF NOT EXISTS interactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id       INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    type          TEXT NOT NULL,
    direction     TEXT DEFAULT 'out',
    date          TEXT,
    subject       TEXT,
    content       TEXT,
    outcome       TEXT,
    next_step     TEXT,
    follow_up_date TEXT,
    automated     INTEGER DEFAULT 0,
    created_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_inter_lead ON interactions(lead_id);
CREATE INDEX IF NOT EXISTS idx_inter_date ON interactions(date);

CREATE TABLE IF NOT EXISTS searches (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    niche             TEXT,
    city              TEXT,
    area              TEXT,
    country           TEXT,
    provider          TEXT,
    max_results       INTEGER,
    results_found     INTEGER DEFAULT 0,
    new_leads         INTEGER DEFAULT 0,
    duplicates_skipped INTEGER DEFAULT 0,
    hot_count         INTEGER DEFAULT 0,
    warm_count        INTEGER DEFAULT 0,
    cold_count        INTEGER DEFAULT 0,
    api_calls         INTEGER DEFAULT 0,
    status            TEXT DEFAULT 'RUNNING',
    progress          TEXT,
    error_message     TEXT,
    triggered_by      TEXT DEFAULT 'manual',
    started_at        TEXT,
    completed_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_searches_started ON searches(started_at);

CREATE TABLE IF NOT EXISTS search_coverage (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    city                TEXT NOT NULL,
    area                TEXT NOT NULL DEFAULT '',
    pincode             TEXT,
    niche               TEXT NOT NULL,
    country             TEXT NOT NULL DEFAULT '',
    last_searched       TEXT,
    times_searched      INTEGER DEFAULT 0,
    total_leads_found   INTEGER DEFAULT 0,
    new_leads_last_search INTEGER DEFAULT 0,
    barren_runs         INTEGER DEFAULT 0,
    status              TEXT DEFAULT 'NEVER',
    created_at          TEXT,
    updated_at          TEXT,
    UNIQUE(city, area, niche, country)
);

CREATE TABLE IF NOT EXISTS targets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    niche            TEXT NOT NULL,
    city             TEXT NOT NULL,
    area             TEXT DEFAULT '',
    country          TEXT DEFAULT '',
    max_results      INTEGER DEFAULT 20,
    is_active        INTEGER DEFAULT 1,
    auto_search      TEXT DEFAULT 'weekly',
    last_searched    TEXT,
    next_search_date TEXT,
    created_at       TEXT,
    UNIQUE(niche, city, area, country)
);

CREATE TABLE IF NOT EXISTS templates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    type          TEXT NOT NULL,
    stage         TEXT,
    subject       TEXT,
    body          TEXT NOT NULL,
    language      TEXT DEFAULT 'en',
    target_market TEXT DEFAULT 'india',
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_stages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,
    position            INTEGER,
    color               TEXT,
    auto_follow_up_days INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS suppression_list (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    value      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'email',
    reason     TEXT,
    created_at TEXT,
    UNIQUE(value, kind)
);

CREATE TABLE IF NOT EXISTS approval_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id       INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    channel       TEXT NOT NULL DEFAULT 'email',
    template_name TEXT,
    step          TEXT,
    to_address    TEXT,
    subject       TEXT,
    body          TEXT,
    status        TEXT DEFAULT 'PENDING',
    reviewed_at   TEXT,
    sent_at       TEXT,
    error_message TEXT,
    created_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_approval_status ON approval_queue(status);

CREATE TABLE IF NOT EXISTS api_usage (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    day      TEXT NOT NULL,
    service  TEXT NOT NULL,
    tier     TEXT NOT NULL DEFAULT 'default',
    calls    INTEGER DEFAULT 0,
    UNIQUE(day, service, tier)
);

CREATE TABLE IF NOT EXISTS failed_jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job        TEXT NOT NULL,
    ref_id     INTEGER,
    error      TEXT,
    payload    TEXT,
    resolved   INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS automation_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job        TEXT,
    message    TEXT,
    level      TEXT DEFAULT 'info',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_autolog_created ON automation_log(created_at);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Exactly one process may run the scheduler. Without this, `uvicorn --workers 4`
-- gives you four schedulers: four morning searches, four backups, four digests.
-- The lease expires, so if the holder dies another worker takes over.
CREATE TABLE IF NOT EXISTS scheduler_lease (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    owner      TEXT,
    expires_at TEXT
);

-- One row per operator alert already sent (Part 7.6). The UNIQUE key is what
-- makes alerting idempotent: a HOT lead alerts once ever, a quota warning once
-- a day, however many times the code paths that raise them are re-entered.
CREATE TABLE IF NOT EXISTS alerts_sent (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_key  TEXT NOT NULL UNIQUE,
    kind       TEXT,
    created_at TEXT
);
"""

DEFAULT_SWITCHES = {
    "automation_master": "off",
    "job_discovery": "on",
    "job_enrichment": "on",
    "job_followup_generation": "on",
    "job_sending": "off",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


_local = threading.local()


def _new_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=settings.db_timeout,
                           isolation_level=None)
    conn.row_factory = sqlite3.Row
    # journal_mode is persisted in the database file itself, so it only needs
    # setting once (init_db does it) - re-issuing it per connection is one of
    # the slow calls this module exists to avoid.
    conn.execute(f"PRAGMA busy_timeout={settings.db_timeout * 1000}")
    conn.execute("PRAGMA foreign_keys=ON")
    # NORMAL is the standard pairing for WAL: it stops fsync-ing on every single
    # commit, which is what held writes to ~15/sec. The tradeoff is that an OS
    # crash or power loss can lose the last few transactions - it cannot corrupt
    # the database. For a lead tracker with nightly backups that is the right
    # side of the trade; set DB_SYNCHRONOUS=FULL if you disagree.
    conn.execute(f"PRAGMA synchronous={settings.db_synchronous}")
    conn.execute(f"PRAGMA cache_size=-{settings.db_cache_mb * 1024}")
    conn.execute("PRAGMA temp_store=MEMORY")
    if settings.db_mmap_mb:
        conn.execute(f"PRAGMA mmap_size={settings.db_mmap_mb * 1024 * 1024}")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield this thread's connection. Deliberately does not close it - the
    handle is reused for every later call on the same thread."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _local.conn = _new_conn()
    yield conn


def close_conn() -> None:
    """Drop this thread's connection. Only needed when a thread is about to
    exit, or when the database file underneath has been swapped (the tests)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        finally:
            _local.conn = None


def query(sql: str, params: tuple | dict = ()) -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | dict = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple | dict = ()) -> int:
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid or cur.rowcount


def scalar(sql: str, params: tuple | dict = (), default: Any = 0) -> Any:
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    if row is None or row[0] is None:
        return default
    return row[0]


def get_setting(key: str, default: str | None = None) -> str | None:
    row = query_one("SELECT value FROM app_settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    execute(
        "INSERT INTO app_settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def log_automation(job: str, message: str, level: str = "info") -> None:
    execute(
        "INSERT INTO automation_log(job, message, level, created_at) VALUES(?,?,?,?)",
        (job, message, level, utcnow()),
    )


def log_failure(job: str, error: str, ref_id: int | None = None, payload: Any = None) -> None:
    execute(
        "INSERT INTO failed_jobs(job, ref_id, error, payload, created_at) VALUES(?,?,?,?,?)",
        (job, ref_id, str(error)[:2000], json.dumps(payload) if payload else None, utcnow()),
    )


def init_db() -> None:
    with get_conn() as conn:
        # Persisted in the database file, so this is the only place it's needed.
        # WAL is what lets the scheduler write while the UI reads.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    for name, pos, color, days in PIPELINE_STAGES:
        execute(
            "INSERT OR IGNORE INTO pipeline_stages(name, position, color, auto_follow_up_days) "
            "VALUES(?,?,?,?)",
            (name, pos, color, days),
        )
    for key, value in DEFAULT_SWITCHES.items():
        if get_setting(key) is None:
            set_setting(key, value)
    if get_setting("automation_master") is None:
        set_setting("automation_master", "on" if settings.automation_enabled else "off")

    from .seed import seed_templates

    seed_templates()
