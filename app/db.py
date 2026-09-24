"""MySQL access layer + schema.

One connection **per thread**, reused for that thread's lifetime. A database
connection is not safe to share across threads, but opening a fresh one per
query is what actually costs. Keeping the handle on thread-local storage
removes that entirely, and request threads, scheduler threads and the
enrichment pool are all bounded and reused, so the number of live connections
stays small.

Raw SQL, no ORM - keeps the dependency list tiny and the queries obvious.
The application writes `?` placeholders everywhere; `TranslateCursor` rewrites
them to `%s` at execution time so PyMySQL never sees a raw `?`.
"""

from __future__ import annotations

import json
import threading
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import pymysql
from pymysql.cursors import DictCursor

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

_QP = re.compile(r"\?")


class TranslateCursor(DictCursor):
    """DictCursor that accepts the app's `?` placeholders as if they were `%s`.

    Every query in this codebase is written with SQLite-style `?` markers. This
    cursor rewrites them to `%s` on the way through, so the rest of the app
    never needs to know which database it is really talking to.
    """

    def execute(self, query: str, args=None):
        return super().execute(_QP.sub("%s", query), args)

    def executemany(self, query: str, args=None):
        return super().executemany(_QP.sub("%s", query), args)


# ------------------------------------------------------------------- schema --

CREATE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS leads (
        id                      INT AUTO_INCREMENT PRIMARY KEY,
        business_name           VARCHAR(255) NOT NULL,
        owner_name              VARCHAR(255),
        phone                   VARCHAR(64),
        phone_valid             INT DEFAULT 0,
        email                   VARCHAR(255),
        email_valid             INT DEFAULT 0,
        whatsapp                VARCHAR(64),
        website_url             VARCHAR(512),
        address                 TEXT,
        city                    VARCHAR(128),
        area                    VARCHAR(128),
        pincode                 VARCHAR(16),
        state                   VARCHAR(128),
        country                 VARCHAR(128),
        lat                     DOUBLE,
        lng                     DOUBLE,
        place_id                VARCHAR(191),
        google_maps_url         VARCHAR(512),
        google_rating           DOUBLE,
        google_reviews          INT,
        instagram_url           VARCHAR(512),
        facebook_url            VARCHAR(512),
        linkedin_url            VARCHAR(512),
        has_website             INT,
        website_score           INT,
        website_issues          TEXT,
        website_platform        VARCHAR(64),
        website_ssl             INT,
        website_mobile_friendly INT,
        website_speed_ms        INT,
        website_last_updated    VARCHAR(40),
        website_checked_at      VARCHAR(40),
        lead_score              INT DEFAULT 0,
        lead_priority           VARCHAR(16) DEFAULT 'COLD',
        data_quality_score      INT DEFAULT 0,
        score_reasons           TEXT,
        status                  VARCHAR(32) DEFAULT 'NEW',
        source                  VARCHAR(64),
        niche                   VARCHAR(128),
        tags                    TEXT,
        notes                   TEXT,
        estimated_budget        VARCHAR(64),
        business_size           VARCHAR(64),
        business_status         VARCHAR(32) DEFAULT 'OPERATIONAL',
        enrichment_status       VARCHAR(16) DEFAULT 'pending',
        enrichment_error        TEXT,
        first_contacted_date    VARCHAR(40),
        last_contacted_date     VARCHAR(40),
        follow_up_date          VARCHAR(40),
        total_touchpoints       INT DEFAULT 0,
        contact_method          VARCHAR(32),
        search_id               INT,
        found_date              VARCHAR(40),
        created_at              VARCHAR(40),
        updated_at              VARCHAR(40),
        UNIQUE KEY uq_leads_place_id (place_id),
        KEY idx_leads_status    (status),
        KEY idx_leads_priority  (lead_priority),
        KEY idx_leads_city      (city),
        KEY idx_leads_niche     (niche),
        KEY idx_leads_followup  (follow_up_date),
        KEY idx_leads_namecity  (business_name, city),
        KEY idx_leads_phone     (phone),
        KEY idx_leads_enrich    (enrichment_status),
        KEY idx_leads_search_id (search_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS interactions (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        lead_id       INT NOT NULL,
        type          VARCHAR(32) NOT NULL,
        direction     VARCHAR(8) DEFAULT 'out',
        date          VARCHAR(40),
        subject       TEXT,
        content       TEXT,
        outcome       VARCHAR(128),
        next_step     VARCHAR(255),
        follow_up_date VARCHAR(40),
        automated     INT DEFAULT 0,
        created_at    VARCHAR(40),
        KEY idx_inter_lead (lead_id),
        KEY idx_inter_date (date),
        CONSTRAINT fk_inter_lead FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS searches (
        id                INT AUTO_INCREMENT PRIMARY KEY,
        user_id           INT,
        niche             VARCHAR(128),
        city              VARCHAR(128),
        area              VARCHAR(128),
        country           VARCHAR(128),
        provider          VARCHAR(32),
        max_results       INT,
        results_found     INT DEFAULT 0,
        new_leads         INT DEFAULT 0,
        duplicates_skipped INT DEFAULT 0,
        hot_count         INT DEFAULT 0,
        warm_count        INT DEFAULT 0,
        cold_count        INT DEFAULT 0,
        api_calls         INT DEFAULT 0,
        status            VARCHAR(24) DEFAULT 'RUNNING',
        progress          TEXT,
        error_message     TEXT,
        triggered_by      VARCHAR(24) DEFAULT 'manual',
        started_at        VARCHAR(40),
        completed_at      VARCHAR(40),
        KEY idx_searches_started (started_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS search_coverage (
        id                  INT AUTO_INCREMENT PRIMARY KEY,
        city                VARCHAR(128) NOT NULL,
        area                VARCHAR(128) NOT NULL DEFAULT '',
        pincode             VARCHAR(16),
        niche               VARCHAR(128) NOT NULL,
        country             VARCHAR(128) NOT NULL DEFAULT '',
        last_searched       VARCHAR(40),
        times_searched      INT DEFAULT 0,
        total_leads_found   INT DEFAULT 0,
        new_leads_last_search INT DEFAULT 0,
        barren_runs         INT DEFAULT 0,
        status              VARCHAR(16) DEFAULT 'NEVER',
        created_at          VARCHAR(40),
        updated_at          VARCHAR(40),
        UNIQUE KEY uq_coverage (city, area, niche, country)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS targets (
        id               INT AUTO_INCREMENT PRIMARY KEY,
        niche            VARCHAR(128) NOT NULL,
        city             VARCHAR(128) NOT NULL,
        area             VARCHAR(128) DEFAULT '',
        country          VARCHAR(128) DEFAULT '',
        max_results      INT DEFAULT 20,
        is_active        INT DEFAULT 1,
        auto_search      VARCHAR(16) DEFAULT 'weekly',
        last_searched    VARCHAR(40),
        next_search_date VARCHAR(16),
        created_at       VARCHAR(40),
        UNIQUE KEY uq_target (niche, city, area, country)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS templates (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        name          VARCHAR(128) NOT NULL,
        type          VARCHAR(16) NOT NULL,
        stage         VARCHAR(32),
        subject       TEXT,
        body          TEXT NOT NULL,
        language      VARCHAR(8) DEFAULT 'en',
        target_market VARCHAR(16) DEFAULT 'india',
        created_at    VARCHAR(40),
        UNIQUE KEY uq_template_name (name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS pipeline_stages (
        id                  INT AUTO_INCREMENT PRIMARY KEY,
        name                VARCHAR(32) NOT NULL,
        position            INT,
        color               VARCHAR(16),
        auto_follow_up_days INT DEFAULT 0,
        UNIQUE KEY uq_stage_name (name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS suppression_list (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        value      VARCHAR(255) NOT NULL,
        kind       VARCHAR(16) NOT NULL DEFAULT 'email',
        reason     VARCHAR(255),
        created_at VARCHAR(40),
        UNIQUE KEY uq_suppression (value, kind)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS approval_queue (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        lead_id       INT NOT NULL,
        channel       VARCHAR(16) NOT NULL DEFAULT 'email',
        template_name VARCHAR(128),
        step          VARCHAR(32),
        to_address    VARCHAR(255),
        subject       TEXT,
        body          TEXT,
        status        VARCHAR(16) DEFAULT 'PENDING',
        reviewed_at   VARCHAR(40),
        sent_at       VARCHAR(40),
        error_message TEXT,
        created_at    VARCHAR(40),
        KEY idx_approval_status (status),
        CONSTRAINT fk_approval_lead FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS api_usage (
        id      INT AUTO_INCREMENT PRIMARY KEY,
        day     VARCHAR(16) NOT NULL,
        service VARCHAR(32) NOT NULL,
        tier    VARCHAR(16) NOT NULL DEFAULT 'default',
        calls   INT DEFAULT 0,
        UNIQUE KEY uq_api_usage (day, service, tier)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS failed_jobs (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        job        VARCHAR(64) NOT NULL,
        ref_id     INT,
        error      TEXT,
        payload    TEXT,
        resolved   INT DEFAULT 0,
        created_at VARCHAR(40)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS automation_log (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        job        VARCHAR(64),
        message    TEXT,
        level      VARCHAR(8) DEFAULT 'info',
        created_at VARCHAR(40),
        KEY idx_autolog_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        setting_key VARCHAR(191) PRIMARY KEY,
        value TEXT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    # Exactly one process may run the scheduler. Without this, `uvicorn
    # --workers 4` gives you four schedulers: four morning searches, four
    # backups, four digests. The lease expires, so if the holder dies another
    # worker takes over.
    """
    CREATE TABLE IF NOT EXISTS scheduler_lease (
        id         INT PRIMARY KEY,
        owner      VARCHAR(128),
        expires_at VARCHAR(40),
        CHECK (id = 1)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    # One row per operator alert already sent (Part 7.6). The UNIQUE key is what
    # makes alerting idempotent: a HOT lead alerts once ever, a quota warning
    # once a day, however many times the code paths that raise them are
    # re-entered.
    """
    CREATE TABLE IF NOT EXISTS alerts_sent (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        alert_key  VARCHAR(191) NOT NULL,
        kind       VARCHAR(16),
        created_at VARCHAR(40),
        UNIQUE KEY uq_alert_key (alert_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    # Multi-user accounts. superadmin is seeded from ADMIN_USER/ADMIN_PASSWORD
    # on first run; admin creates the rest from the Users page.
    """
    CREATE TABLE IF NOT EXISTS users (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        username      VARCHAR(64) NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        full_name     VARCHAR(255),
        email         VARCHAR(255),
        role          VARCHAR(16) NOT NULL DEFAULT 'user',
        is_active     INT DEFAULT 1,
        created_at    VARCHAR(40),
        updated_at    VARCHAR(40),
        UNIQUE KEY uq_username (username)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    # Subscription plans - what a user is subscribed to, and the limits that
    # gate lead searching. Created and managed from the Plans page (admin+).
    """
    CREATE TABLE IF NOT EXISTS plans (
        id                    INT AUTO_INCREMENT PRIMARY KEY,
        name                  VARCHAR(128) NOT NULL,
        description           TEXT,
        price                 DECIMAL(10,2) DEFAULT 0,
        currency              VARCHAR(8) DEFAULT 'INR',
        period_days           INT DEFAULT 30,
        search_limit          INT DEFAULT 50,
        max_results_per_search INT DEFAULT 20,
        is_active             INT DEFAULT 1,
        created_at            VARCHAR(40),
        updated_at            VARCHAR(40),
        UNIQUE KEY uq_plan_name (name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
    """
    CREATE TABLE IF NOT EXISTS user_subscriptions (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        user_id       INT NOT NULL,
        plan_id       INT NOT NULL,
        status        VARCHAR(16) NOT NULL DEFAULT 'pending',
        period_start  VARCHAR(40),
        period_end    VARCHAR(40),
        searches_used INT DEFAULT 0,
        channel       VARCHAR(8) NOT NULL DEFAULT 'admin',
        payment_status VARCHAR(16) NOT NULL DEFAULT 'unpaid',
        payment_ref   VARCHAR(255),
        created_at    VARCHAR(40),
        updated_at    VARCHAR(40),
        KEY idx_sub_user (user_id),
        KEY idx_sub_plan (plan_id),
        CONSTRAINT fk_sub_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        CONSTRAINT fk_sub_plan FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,
]

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


class Conn(pymysql.connections.Connection):
    """PyMySQL connection that speaks the sqlite3 connection API too.

    The rest of the app was written against sqlite3, whose Connection exposes
    `.execute()`/`.executemany()` directly. Keeping those here means every call
    site works unchanged: `conn.execute(sql, params)` runs a cursor and returns
    it (so `.fetchone()`, `.lastrowid`, `.rowcount` all behave as before).
    """

    def execute(self, query: str, args=None):
        cur = self.cursor()
        cur.execute(query, args)
        return cur

    def executemany(self, query: str, args=None):
        cur = self.cursor()
        cur.executemany(query, args)
        return cur


def _new_conn() -> Conn:
    conn = Conn(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset=settings.mysql_charset,
        autocommit=True,
        connect_timeout=settings.mysql_connect_timeout,
        cursorclass=TranslateCursor,
    )
    return conn


@contextmanager
def get_conn() -> Iterator[pymysql.connections.Connection]:
    """Yield this thread's connection. Deliberately does not close it - the
    handle is reused for every later call on the same thread."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _local.conn = _new_conn()
    yield conn


def close_conn() -> None:
    """Drop this thread's connection. Only needed when a thread is about to
    exit, or when tests point at a fresh database."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        finally:
            _local.conn = None


def query(sql: str, params: tuple | dict = ()) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: tuple | dict = ()) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None


def execute(sql: str, params: tuple | dict = ()) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.lastrowid or cur.rowcount


def scalar(sql: str, params: tuple | dict = (), default: Any = 0) -> Any:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
    if row is None:
        return default
    value = next(iter(row.values()), None)
    if value is None:
        return default
    return value


def get_setting(key: str, default: str | None = None) -> str | None:
    row = query_one("SELECT value FROM app_settings WHERE setting_key = ?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    execute(
        "INSERT INTO app_settings(setting_key, value) VALUES(?, ?) "
        "ON DUPLICATE KEY UPDATE value = VALUES(value)",
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


def _ensure_searches_user_id(conn: Conn) -> None:
    """Apply schema drift that landed after the MySQL move: searches.user_id
    (added with subscriptions). Databases created before that would otherwise
    fail every manual search with "Unknown column 'user_id'". Idempotent."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) AS n FROM information_schema.columns
               WHERE table_schema = DATABASE() AND table_name = 'searches'
                 AND column_name = 'user_id'"""
        )
        if cur.fetchone()["n"] == 0:
            cur.execute("ALTER TABLE searches ADD COLUMN user_id INT AFTER id")
            cur.execute("ALTER TABLE searches ADD KEY idx_search_user (user_id)")


def init_db() -> None:
    """Create tables if missing and seed defaults. Safe to call every boot."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            for statement in CREATE_TABLES:
                cur.execute(statement)
            _ensure_searches_user_id(conn)

    for name, pos, color, days in PIPELINE_STAGES:
        execute(
            "INSERT IGNORE INTO pipeline_stages(name, position, color, auto_follow_up_days) "
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

    # Seed the superadmin from the .env credentials on the very first run.
    if not query_one("SELECT id FROM users WHERE role = 'superadmin'"):
        from .security import hash_password

        now = utcnow()
        execute(
            """INSERT INTO users(username, password_hash, full_name, email, role,
               is_active, created_at, updated_at)
               VALUES(?,?,'Site Owner','','superadmin',1,?,?)""",
            (settings.admin_user, hash_password(settings.admin_password or ""), now, now),
        )