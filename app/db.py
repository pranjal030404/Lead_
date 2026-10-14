"""MySQL access layer + schema.

One connection **per thread**, reused for that thread's lifetime. A database
connection is not safe to share across threads, but opening a fresh one per
query is what actually costs. Keeping the handle on thread-local storage
removes that entirely, and request threads, scheduler threads and the
enrichment pool are all bounded and reused, so the number of live connections
stays small.

Raw SQL, no ORM - keeps the dependency list tiny and the queries obvious. The
application writes `?` placeholders everywhere; `TranslateCursor` rewrites
them to `%s` at execution time so PyMySQL never sees a raw `?`.

The query helpers below default `params` to `None`, never `()`, and that is
load-bearing. PyMySQL runs `query % args` whenever `args` is not None, so an
empty tuple still interpolates - which turns a literal `%` in a
parameterless query (`LIKE 'brand_%'`, `DATE_FORMAT(d, '%Y')`) into a format
specifier and raises `TypeError: not enough arguments for format string` from
inside the DB driver, surfacing as a bare 500. None tells PyMySQL there is
nothing to bind, so literal `%` in the SQL stays literal.
"""

from __future__ import annotations

import contextvars
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
    # Snapshot of the leads a completed search produced. Serving an identical
    # manual query from here means zero provider (API) calls for the second,
    # third... user. fingerprint is a hash of the exact query that defines the
    # result set; a cache entry whose params differ has a different key and is
    # never reused. A refresh replaces search_id/result_count and resets
    # created_at, which is what the TTL is measured from.
    """
    CREATE TABLE IF NOT EXISTS search_cache (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        fingerprint     VARCHAR(64) NOT NULL,
        niche           VARCHAR(128),
        city            VARCHAR(128),
        area            VARCHAR(128),
        country         VARCHAR(128),
        provider        VARCHAR(32),
        max_results     INT,
        lat             DOUBLE,
        lng             DOUBLE,
        radius_m        INT,
        search_id       INT,
        result_count    INT DEFAULT 0,
        created_at      VARCHAR(40),
        updated_at      VARCHAR(40),
        UNIQUE KEY uq_cache_fingerprint (fingerprint),
        KEY idx_cache_search (search_id)
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

# ------------------------------------------------------------ tenant scope --
# The active workspace for the current request / job. Set by the auth middleware
# for HTTP requests and by `workspace_context` for background work. Reading it
# when unset raises NoWorkspace rather than returning a default: a code path that
# forgot to establish a tenant must fail, never fall back to someone's data.
# Note: plain threads do NOT inherit contextvars - background code must pass the
# workspace id explicitly and enter `workspace_context` itself.
_ws_var: contextvars.ContextVar[int | None] = contextvars.ContextVar("workspace_id", default=None)


class NoWorkspace(RuntimeError):
    """A tenant-scoped operation ran with no workspace established."""


def current_ws() -> int:
    wid = _ws_var.get()
    if wid is None:
        raise NoWorkspace("No workspace selected for this operation")
    return wid


def current_ws_or_none() -> int | None:
    return _ws_var.get()


@contextmanager
def workspace_context(workspace_id: int) -> Iterator[int]:
    token = _ws_var.set(int(workspace_id))
    try:
        yield int(workspace_id)
    finally:
        _ws_var.reset(token)


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


def query(sql: str, params: tuple | dict | None = None) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: tuple | dict | None = None) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None


def execute(sql: str, params: tuple | dict | None = None) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.lastrowid or cur.rowcount


def scalar(sql: str, params: tuple | dict | None = None, default: Any = 0) -> Any:
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


def get_ws_setting(key: str, default: str | None = None) -> str | None:
    """Per-workspace setting, falling back to the install-wide value."""
    row = query_one("SELECT value FROM app_settings WHERE setting_key = ?",
                    (f"ws:{current_ws()}:{key}",))
    return row["value"] if row else get_setting(key, default)


def set_ws_setting(key: str, value: str) -> None:
    set_setting(f"ws:{current_ws()}:{key}", value)


def log_automation(job: str, message: str, level: str = "info") -> None:
    """workspace_id is NULL for system-level entries (backups, scheduler)."""
    execute(
        "INSERT INTO automation_log(job, message, level, created_at, workspace_id) "
        "VALUES(?,?,?,?,?)",
        (job, message, level, utcnow(), current_ws_or_none()),
    )


def log_failure(job: str, error: str, ref_id: int | None = None, payload: Any = None) -> None:
    execute(
        "INSERT INTO failed_jobs(job, ref_id, error, payload, created_at, workspace_id) "
        "VALUES(?,?,?,?,?,?)",
        (job, ref_id, str(error)[:2000], json.dumps(payload) if payload else None, utcnow(),
         current_ws_or_none()),
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


def _ensure_searches_cached_from(conn: Conn) -> None:
    """Apply schema drift: searches.cached_from (added with the search cache).
    A search served from the cache stores the id of the search whose leads it
    reused; the status endpoint then lists leads for that original run. NULL for
    searches that called the provider normally. Idempotent."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) AS n FROM information_schema.columns
               WHERE table_schema = DATABASE() AND table_name = 'searches'
                 AND column_name = 'cached_from'"""
        )
        if cur.fetchone()["n"] == 0:
            cur.execute("ALTER TABLE searches ADD COLUMN cached_from INT NULL AFTER user_id")


def _ensure_app_settings_updated_at(conn: Conn) -> None:
    """Apply schema drift: app_settings.updated_at (added with white-label
    branding, which timestamps brand edits). Databases created before that
    would fail every branding write. Idempotent."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) AS n FROM information_schema.columns
               WHERE table_schema = DATABASE() AND table_name = 'app_settings'
                 AND column_name = 'updated_at'"""
        )
        if cur.fetchone()["n"] == 0:
            cur.execute("ALTER TABLE app_settings ADD COLUMN updated_at VARCHAR(40) NULL")


# ----------------------------------------------------- tenancy migrations --

def _col_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) AS n FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = ? AND column_name = ?",
        (table, column),
    )
    return cur.fetchone()["n"] > 0


def _index_exists(cur, table: str, name: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) AS n FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = ? AND index_name = ?",
        (table, name),
    )
    return cur.fetchone()["n"] > 0


def _fk_exists(cur, table: str, name: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) AS n FROM information_schema.table_constraints "
        "WHERE table_schema = DATABASE() AND table_name = ? AND constraint_name = ? "
        "AND constraint_type = 'FOREIGN KEY'",
        (table, name),
    )
    return cur.fetchone()["n"] > 0


def _create_tenancy_tables(conn: Conn) -> None:
    """organizations / workspaces / workspace_members, plus a default org and
    workspace that adopts every pre-existing user and row.

    Rollback: DROP TABLE workspace_members, workspaces, organizations (after
    running the rollback for migration 5, which depends on these)."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS organizations (
                id         INT AUTO_INCREMENT PRIMARY KEY,
                name       VARCHAR(255) NOT NULL,
                slug       VARCHAR(100) NOT NULL,
                is_active  INT DEFAULT 1,
                created_at VARCHAR(40),
                UNIQUE KEY uq_org_slug (slug)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                organization_id INT NOT NULL,
                name            VARCHAR(255) NOT NULL,
                slug            VARCHAR(100) NOT NULL,
                is_active       INT DEFAULT 1,
                created_at      VARCHAR(40),
                UNIQUE KEY uq_ws_slug (organization_id, slug),
                CONSTRAINT fk_ws_org FOREIGN KEY (organization_id)
                    REFERENCES organizations(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
        # `role` is the member's role *inside* the workspace (owner/admin/member).
        # users.role stays the authority for what a person may do until the
        # permission system lands.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS workspace_members (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                workspace_id INT NOT NULL,
                user_id      INT NOT NULL,
                role         VARCHAR(16) NOT NULL DEFAULT 'member',
                created_at   VARCHAR(40),
                UNIQUE KEY uq_ws_member (workspace_id, user_id),
                KEY idx_member_user (user_id),
                CONSTRAINT fk_member_ws FOREIGN KEY (workspace_id)
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                CONSTRAINT fk_member_user FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
        cur.execute("SELECT id FROM workspaces ORDER BY id LIMIT 1")
        if cur.fetchone() is None:
            now = utcnow()
            cur.execute("INSERT INTO organizations(name, slug, created_at) VALUES(?,?,?)",
                        ("Default Organization", "default", now))
            org_id = cur.lastrowid
            cur.execute(
                "INSERT INTO workspaces(organization_id, name, slug, created_at) VALUES(?,?,?,?)",
                (org_id, "Default Workspace", "default", now))
        cur.execute("SELECT id FROM workspaces ORDER BY id LIMIT 1")
        default_ws = cur.fetchone()["id"]
        cur.execute(
            """INSERT IGNORE INTO workspace_members(workspace_id, user_id, role, created_at)
               SELECT ?, id, CASE role WHEN 'superadmin' THEN 'owner'
                                       WHEN 'admin' THEN 'admin' ELSE 'member' END, ?
               FROM users""",
            (default_ws, utcnow()),
        )


# (table, old unique key to replace, new unique columns, derive workspace from
#  the parent lead, nullable). Rows already in the database are adopted by the
#  default workspace - the pre-tenancy behaviour was "everyone shares everything".
_TENANT_TABLES = [
    ("leads", "uq_leads_place_id", ("workspace_id", "place_id"), False, False),
    ("interactions", None, None, True, False),
    ("searches", None, None, False, False),
    ("search_cache", "uq_cache_fingerprint", ("workspace_id", "fingerprint"), False, False),
    ("search_coverage", "uq_coverage",
     ("workspace_id", "city", "area", "niche", "country"), False, False),
    ("targets", "uq_target", ("workspace_id", "niche", "city", "area", "country"), False, False),
    ("templates", "uq_template_name", ("workspace_id", "name"), False, False),
    ("suppression_list", "uq_suppression", ("workspace_id", "value", "kind"), False, False),
    ("approval_queue", None, None, True, False),
    ("failed_jobs", None, None, False, True),
    ("automation_log", None, None, False, True),
]


def _add_workspace_scope(conn: Conn) -> None:
    """Add workspace_id to every tenant table, backfill, tighten, re-key.

    Idempotent and safe to re-run after a partial failure (MySQL DDL is not
    transactional): each step checks current state first. Nothing is dropped
    except the old unique key, and only after its replacement exists.

    NOT NULL with *no default* is deliberate: an INSERT that forgets the tenant
    errors instead of silently landing in workspace 1.

    Rollback: for each table, drop FK fk_<table>_ws, the new unique key and
    idx_<table>_ws, re-add the old unique key, then DROP COLUMN workspace_id.
    (Rolling back after a second workspace holds data would merge tenants.)"""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM workspaces ORDER BY id LIMIT 1")
        default_ws = cur.fetchone()["id"]

        for table, old_unique, new_cols, from_lead, nullable in _TENANT_TABLES:
            if not _col_exists(cur, table, "workspace_id"):
                cur.execute(f"ALTER TABLE {table} ADD COLUMN workspace_id INT NULL")

            if from_lead:
                cur.execute(
                    f"UPDATE {table} t JOIN leads l ON l.id = t.lead_id "
                    "SET t.workspace_id = l.workspace_id WHERE t.workspace_id IS NULL"
                )
            if not nullable:
                cur.execute(f"UPDATE {table} SET workspace_id = ? WHERE workspace_id IS NULL",
                            (default_ws,))
                cur.execute(f"ALTER TABLE {table} MODIFY workspace_id INT NOT NULL")

            idx = f"idx_{table}_ws"
            if not _index_exists(cur, table, idx):
                cur.execute(f"ALTER TABLE {table} ADD KEY {idx} (workspace_id)")

            if old_unique:
                new_name = old_unique + "_ws"
                if not _index_exists(cur, table, new_name):
                    cols = ", ".join(new_cols)
                    cur.execute(f"ALTER TABLE {table} ADD UNIQUE KEY {new_name} ({cols})")
                if _index_exists(cur, table, old_unique):
                    cur.execute(f"ALTER TABLE {table} DROP INDEX {old_unique}")

            if not nullable:
                fk = f"fk_{table}_ws"
                if not _fk_exists(cur, table, fk):
                    cur.execute(
                        f"ALTER TABLE {table} ADD CONSTRAINT {fk} FOREIGN KEY (workspace_id) "
                        "REFERENCES workspaces(id) ON DELETE CASCADE"
                    )


def _create_intel_tables(conn: Conn) -> None:
    """Data-intelligence tables: field lineage, decision-maker contacts, email
    addresses (with classification) and social profiles; plus freshness /
    confidence columns on leads. All tenant-scoped, all cascade with the lead.

    Rollback: DROP TABLE social_profiles, email_addresses, contacts,
    field_sources; ALTER TABLE leads DROP COLUMN data_confidence_score,
    freshness_status, last_verified_at, next_verification_at."""
    ddl_tail = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    with conn.cursor() as cur:
        # One row per (entity, field): where the *current* value came from.
        # A new observation replaces the row and clears verified_at - a changed
        # value is not verified just because the old one was.
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS field_sources (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                workspace_id INT NOT NULL,
                entity_type  VARCHAR(16) NOT NULL DEFAULT 'lead',
                entity_id    INT NOT NULL,
                field        VARCHAR(48) NOT NULL,
                source       VARCHAR(64) NOT NULL,
                source_type  VARCHAR(24) NOT NULL,
                confidence   DOUBLE NOT NULL,
                observed_at  VARCHAR(40) NOT NULL,
                verified_at  VARCHAR(40),
                expires_at   VARCHAR(40),
                UNIQUE KEY uq_field_source (workspace_id, entity_type, entity_id, field),
                KEY idx_fs_entity (entity_type, entity_id),
                CONSTRAINT fk_fs_ws FOREIGN KEY (workspace_id)
                    REFERENCES workspaces(id) ON DELETE CASCADE
            ) {ddl_tail}""")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS contacts (
                id                         INT AUTO_INCREMENT PRIMARY KEY,
                workspace_id               INT NOT NULL,
                lead_id                    INT NOT NULL,
                name                       VARCHAR(255) NOT NULL,
                title                      VARCHAR(255),
                role_category              VARCHAR(24) NOT NULL DEFAULT 'OTHER',
                email                      VARCHAR(255),
                phone                      VARCHAR(64),
                linkedin_url               VARCHAR(512),
                source                     VARCHAR(64) NOT NULL,
                source_type                VARCHAR(24) NOT NULL,
                confidence                 DOUBLE NOT NULL,
                verified_at                VARCHAR(40),
                decision_maker_probability DOUBLE NOT NULL DEFAULT 0,
                notes                      TEXT,
                created_at                 VARCHAR(40),
                updated_at                 VARCHAR(40),
                UNIQUE KEY uq_contact (workspace_id, lead_id, name),
                KEY idx_contact_dm (workspace_id, decision_maker_probability),
                CONSTRAINT fk_contact_ws FOREIGN KEY (workspace_id)
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                CONSTRAINT fk_contact_lead FOREIGN KEY (lead_id)
                    REFERENCES leads(id) ON DELETE CASCADE
            ) {ddl_tail}""")
        # email_type: PERSONAL | GENERIC | ROLE.
        # status:     UNVERIFIED | VERIFIED | INFERRED | INVALID | DISPOSABLE.
        # VERIFIED is only ever written by a verification provider.
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS email_addresses (
                id               INT AUTO_INCREMENT PRIMARY KEY,
                workspace_id     INT NOT NULL,
                lead_id          INT NOT NULL,
                contact_id       INT NULL,
                email            VARCHAR(255) NOT NULL,
                email_type       VARCHAR(16) NOT NULL,
                status           VARCHAR(16) NOT NULL,
                confidence       DOUBLE NOT NULL,
                source           VARCHAR(64) NOT NULL,
                source_type      VARCHAR(24) NOT NULL,
                last_verified_at VARCHAR(40),
                created_at       VARCHAR(40),
                updated_at       VARCHAR(40),
                UNIQUE KEY uq_email_lead (workspace_id, lead_id, email),
                KEY idx_email_addr (workspace_id, email),
                CONSTRAINT fk_email_ws FOREIGN KEY (workspace_id)
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                CONSTRAINT fk_email_lead FOREIGN KEY (lead_id)
                    REFERENCES leads(id) ON DELETE CASCADE,
                CONSTRAINT fk_email_contact FOREIGN KEY (contact_id)
                    REFERENCES contacts(id) ON DELETE SET NULL
            ) {ddl_tail}""")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS social_profiles (
                id               INT AUTO_INCREMENT PRIMARY KEY,
                workspace_id     INT NOT NULL,
                lead_id          INT NOT NULL,
                platform         VARCHAR(24) NOT NULL,
                url              VARCHAR(512) NOT NULL,
                username         VARCHAR(191),
                followers        INT NULL,
                post_frequency   DOUBLE NULL,
                last_activity    VARCHAR(40) NULL,
                match_confidence DOUBLE NOT NULL,
                source           VARCHAR(64) NOT NULL,
                source_type      VARCHAR(24) NOT NULL,
                created_at       VARCHAR(40),
                updated_at       VARCHAR(40),
                UNIQUE KEY uq_social (workspace_id, lead_id, platform),
                CONSTRAINT fk_social_ws FOREIGN KEY (workspace_id)
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                CONSTRAINT fk_social_lead FOREIGN KEY (lead_id)
                    REFERENCES leads(id) ON DELETE CASCADE
            ) {ddl_tail}""")
        for column, ddl in (
            ("data_confidence_score", "INT NULL"),
            ("freshness_status", "VARCHAR(16) NULL"),
            ("last_verified_at", "VARCHAR(40) NULL"),
            ("next_verification_at", "VARCHAR(40) NULL"),
        ):
            if not _col_exists(cur, "leads", column):
                cur.execute(f"ALTER TABLE leads ADD COLUMN {column} {ddl}")
        if not _index_exists(cur, "leads", "idx_leads_freshness"):
            cur.execute("ALTER TABLE leads ADD KEY idx_leads_freshness "
                        "(workspace_id, freshness_status)")


def _create_audit_tables(conn: Conn) -> None:
    """Website audits (history kept) and detected opportunities, plus summary
    columns on leads for filtering/sorting.

    Rollback: DROP TABLE lead_opportunities, website_audits; ALTER TABLE leads
    DROP COLUMN website_audit_score, website_audited_at, opportunity_count,
    opportunity_value_est."""
    tail = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS website_audits (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                workspace_id    INT NOT NULL,
                lead_id         INT NOT NULL,
                url             VARCHAR(512) NOT NULL,
                final_url       VARCHAR(512),
                status          VARCHAR(16) NOT NULL,
                audit_version   INT NOT NULL,
                overall_score   INT NULL,
                technical_score INT NULL,
                seo_score       INT NULL,
                performance_score INT NULL,
                conversion_score INT NULL,
                content_score   INT NULL,
                findings        LONGTEXT,
                pages_fetched   INT DEFAULT 0,
                duration_ms     INT DEFAULT 0,
                error           TEXT,
                created_at      VARCHAR(40) NOT NULL,
                KEY idx_audit_lead (workspace_id, lead_id, id),
                CONSTRAINT fk_audit_ws FOREIGN KEY (workspace_id)
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                CONSTRAINT fk_audit_lead FOREIGN KEY (lead_id)
                    REFERENCES leads(id) ON DELETE CASCADE
            ) {tail}""")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS lead_opportunities (
                id               INT AUTO_INCREMENT PRIMARY KEY,
                workspace_id     INT NOT NULL,
                lead_id          INT NOT NULL,
                audit_id         INT NULL,
                opportunity_type VARCHAR(40) NOT NULL,
                severity         VARCHAR(8) NOT NULL,
                confidence       DOUBLE NOT NULL,
                evidence         TEXT,
                business_impact  TEXT,
                recommended_solution TEXT,
                est_value_min    INT,
                est_value_max    INT,
                currency         VARCHAR(8) DEFAULT 'INR',
                status           VARCHAR(12) NOT NULL DEFAULT 'OPEN',
                detected_at      VARCHAR(40),
                updated_at       VARCHAR(40),
                resolved_at      VARCHAR(40),
                UNIQUE KEY uq_opportunity (workspace_id, lead_id, opportunity_type),
                KEY idx_opp_ws_status (workspace_id, status, severity),
                CONSTRAINT fk_opp_ws FOREIGN KEY (workspace_id)
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                CONSTRAINT fk_opp_lead FOREIGN KEY (lead_id)
                    REFERENCES leads(id) ON DELETE CASCADE
            ) {tail}""")
        for column, ddl in (
            ("website_audit_score", "INT NULL"), ("website_audited_at", "VARCHAR(40) NULL"),
            ("opportunity_count", "INT NOT NULL DEFAULT 0"),
            ("opportunity_value_est", "INT NOT NULL DEFAULT 0"),
        ):
            if not _col_exists(cur, "leads", column):
                cur.execute(f"ALTER TABLE leads ADD COLUMN {column} {ddl}")


def _create_scoring_tables(conn: Conn) -> None:
    """Multi-score model, intent signals, pinned competitors.

    lead_scores holds the *current* scores and the full explanation (one row per
    lead); lead_score_history keeps the trend. Rollback: DROP TABLE
    lead_competitors, lead_signals, lead_score_history, lead_scores; drop the
    five new `leads` columns."""
    tail = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS lead_scores (
                id               INT AUTO_INCREMENT PRIMARY KEY,
                workspace_id     INT NOT NULL,
                lead_id          INT NOT NULL,
                scoring_version  INT NOT NULL,
                rule_score       INT NULL,
                lead_quality     INT NULL,
                digital_gap      INT NULL,
                contactability   INT NULL,
                business_value   INT NULL,
                buying_intent    INT NULL,
                decision_maker   INT NULL,
                outreach_fit     INT NULL,
                data_confidence  INT NULL,
                overall          INT NULL,
                tier             VARCHAR(8),
                confidence       INT NULL,
                explanation      LONGTEXT,
                computed_at      VARCHAR(40) NOT NULL,
                UNIQUE KEY uq_lead_score (workspace_id, lead_id),
                KEY idx_score_overall (workspace_id, overall),
                CONSTRAINT fk_score_ws FOREIGN KEY (workspace_id)
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                CONSTRAINT fk_score_lead FOREIGN KEY (lead_id)
                    REFERENCES leads(id) ON DELETE CASCADE
            ) {tail}""")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS lead_score_history (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                workspace_id INT NOT NULL,
                lead_id      INT NOT NULL,
                overall      INT NULL,
                tier         VARCHAR(8),
                buying_intent INT NULL,
                created_at   VARCHAR(40) NOT NULL,
                KEY idx_hist_lead (workspace_id, lead_id, id),
                CONSTRAINT fk_hist_ws FOREIGN KEY (workspace_id)
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                CONSTRAINT fk_hist_lead FOREIGN KEY (lead_id)
                    REFERENCES leads(id) ON DELETE CASCADE
            ) {tail}""")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS lead_signals (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                workspace_id INT NOT NULL,
                lead_id      INT NOT NULL,
                signal_type  VARCHAR(40) NOT NULL,
                strength     DOUBLE NOT NULL,
                confidence   DOUBLE NOT NULL,
                evidence     TEXT,
                source       VARCHAR(40) NOT NULL,
                status       VARCHAR(10) NOT NULL DEFAULT 'ACTIVE',
                detected_at  VARCHAR(40) NOT NULL,
                expires_at   VARCHAR(40) NULL,
                updated_at   VARCHAR(40),
                UNIQUE KEY uq_signal (workspace_id, lead_id, signal_type),
                KEY idx_signal_status (workspace_id, status),
                CONSTRAINT fk_signal_ws FOREIGN KEY (workspace_id)
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                CONSTRAINT fk_signal_lead FOREIGN KEY (lead_id)
                    REFERENCES leads(id) ON DELETE CASCADE
            ) {tail}""")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS lead_competitors (
                id                 INT AUTO_INCREMENT PRIMARY KEY,
                workspace_id       INT NOT NULL,
                lead_id            INT NOT NULL,
                competitor_lead_id INT NOT NULL,
                created_at         VARCHAR(40),
                UNIQUE KEY uq_competitor (workspace_id, lead_id, competitor_lead_id),
                CONSTRAINT fk_comp_ws FOREIGN KEY (workspace_id)
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                CONSTRAINT fk_comp_lead FOREIGN KEY (lead_id)
                    REFERENCES leads(id) ON DELETE CASCADE,
                CONSTRAINT fk_comp_other FOREIGN KEY (competitor_lead_id)
                    REFERENCES leads(id) ON DELETE CASCADE
            ) {tail}""")
        for column, ddl in (
            ("overall_score", "INT NULL"), ("opportunity_tier", "VARCHAR(8) NULL"),
            ("score_confidence", "INT NULL"), ("intent_score", "INT NULL"),
            ("scored_at", "VARCHAR(40) NULL"),
        ):
            if not _col_exists(cur, "leads", column):
                cur.execute(f"ALTER TABLE leads ADD COLUMN {column} {ddl}")
        if not _index_exists(cur, "leads", "idx_leads_overall"):
            cur.execute("ALTER TABLE leads ADD KEY idx_leads_overall (workspace_id, overall_score)")


# Fields whose provenance is tracked (kept in sync with app/lineage.TRACKED_FIELDS).
_LINEAGE_BACKFILL_FIELDS = (
    "phone", "email", "whatsapp", "website_url", "address", "owner_name",
    "instagram_url", "facebook_url", "linkedin_url", "google_rating", "google_reviews",
)


def _backfill_lineage(conn: Conn) -> None:
    """Give every pre-existing lead's populated fields a lineage row so nothing
    is silently 'unknown'. Honest labels: source = the lead's recorded source (or
    'unknown'), type 'legacy', confidence 0.5, observed = lead creation time.
    INSERT IGNORE makes it safe to re-run and never overwrites real lineage."""
    with conn.cursor() as cur:
        for field in _LINEAGE_BACKFILL_FIELDS:
            cur.execute(
                f"""INSERT IGNORE INTO field_sources
                    (workspace_id, entity_type, entity_id, field, source, source_type,
                     confidence, observed_at)
                    SELECT workspace_id, 'lead', id, '{field}',
                           COALESCE(NULLIF(source, ''), 'unknown'), 'legacy', 0.5,
                           COALESCE(created_at, ?)
                    FROM leads WHERE {field} IS NOT NULL AND {field} != ''""",
                (utcnow(),),
            )


# Ordered, append-only. Each step must be idempotent and non-destructive (no
# DROP / column removal / data rewrite): a step is recorded in schema_migrations
# once applied, and rollback guidance lives in its docstring. Never edit or
# reorder an applied entry - add a new one.
MIGRATIONS: list[tuple[int, str, Any]] = [
    (1, "searches.user_id", _ensure_searches_user_id),
    (2, "searches.cached_from", _ensure_searches_cached_from),
    (3, "app_settings.updated_at", _ensure_app_settings_updated_at),
    (4, "tenancy tables", _create_tenancy_tables),
    (5, "workspace_id on tenant tables", _add_workspace_scope),
    (6, "lineage / contacts / emails / socials", _create_intel_tables),
    (7, "backfill field lineage for existing leads", _backfill_lineage),
    (8, "website audits and opportunities", _create_audit_tables),
    (9, "lead scores, signals, competitors", _create_scoring_tables),
]

CREATE_MIGRATIONS_TABLE = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version    INT PRIMARY KEY,
        name       VARCHAR(191) NOT NULL,
        applied_at VARCHAR(40)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def run_migrations(conn: Conn) -> list[int]:
    """Apply pending migrations in order; returns the versions applied now."""
    applied_now: list[int] = []
    with conn.cursor() as cur:
        cur.execute(CREATE_MIGRATIONS_TABLE)
        cur.execute("SELECT version FROM schema_migrations")
        done = {row["version"] for row in cur.fetchall()}
    for version, name, step in MIGRATIONS:
        if version in done:
            continue
        step(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES(?,?,?)",
                (version, name, utcnow()),
            )
        applied_now.append(version)
    return applied_now


def init_db() -> None:
    """Create tables if missing and seed defaults. Safe to call every boot."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            for statement in CREATE_TABLES:
                cur.execute(statement)
        run_migrations(conn)

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

    for ws in query("SELECT id FROM workspaces ORDER BY id"):
        with workspace_context(ws["id"]):
            seed_templates()

    # Seed the superadmin from the .env credentials on the very first run, as
    # owner of the default workspace.
    if not query_one("SELECT id FROM users WHERE role = 'superadmin'"):
        from .security import hash_password
        from .tenancy import add_member, default_workspace_id

        now = utcnow()
        user_id = execute(
            """INSERT INTO users(username, password_hash, full_name, email, role,
               is_active, created_at, updated_at)
               VALUES(?,?,'Site Owner','','superadmin',1,?,?)""",
            (settings.admin_user, hash_password(settings.admin_password or ""), now, now),
        )
        add_member(default_workspace_id(), user_id, "owner")
