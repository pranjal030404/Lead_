"""One-time migration: SQLite -> MySQL (Part of moving off SQLite).

Copies every table the app grew in its old SQLite database into the MySQL
database configured by .env. Run it once after switching MYSQL_* settings; the
app then runs purely on MySQL. Original IDs are preserved so foreign keys
(interactions.lead_id, approval_queue.lead_id) stay intact.

New MySQL-only tables (users, plans, user_subscriptions) are left alone -
they are created and seeded fresh by the app. The `app_settings` column `key`
was renamed `setting_key` (MySQL reserves `key`), and that single rename is
applied here.

Usage:
    python scripts/migrate_sqlite_to_mysql.py [sqlite-path]

Reads the path from the first argument, else MYSQL_SQLITE_PATH, else the
default `data/leadgen.db`. Writes to the MYSQL_* connection from .env.
This is a migration run - it is NOT safe to rerun on a MySQL database that has
already gained new rows (INSERT IGNORE keeps old rows, and COUNTs won't match).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402

# Order matters: parents before children so foreign keys resolve.
TABLES = [
    "leads",
    "interactions",
    "searches",
    "search_coverage",
    "targets",
    "templates",
    "pipeline_stages",
    "suppression_list",
    "approval_queue",
    "api_usage",
    "failed_jobs",
    "automation_log",
    "app_settings",
    "scheduler_lease",
    "alerts_sent",
]

COLUMN_RENAMES = {"app_settings": {"key": "setting_key"}}


def _columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    """(sqlite_column, mysql_column) pairs for one table."""
    renames = COLUMN_RENAMES.get(table, {})
    return [
        (row[1], renames.get(row[1], row[1]))
        for row in conn.execute(f"PRAGMA table_info(`{table}`)")
    ]


def main(sqlite_path: str) -> None:
    source = Path(sqlite_path)
    if not source.exists():
        raise SystemExit(f"SQLite database not found: {source}")

    print(f"Reading from SQLite: {source}")
    init_db()  # make sure every MySQL table exists before we insert into it

    sqlite = sqlite3.connect(source)
    sqlite.row_factory = sqlite3.Row

    with get_conn() as mysql:
        with mysql.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            totals = {}
            for table in TABLES:
                cols = _columns(sqlite, table)
                mysql_cols = [m for _, m in cols]
                rows = sqlite.execute(f"SELECT * FROM `{table}`").fetchall()
                if not rows:
                    continue
                placeholders = ", ".join("%s" for _ in cols)
                column_list = ", ".join(f"`{m}`" for m in mysql_cols)
                inserted = 0
                for row in rows:
                    values = tuple(row)  # sqlite3.Row iterates in column order
                    affected = cur.execute(
                        f"INSERT IGNORE INTO `{table}` ({column_list}) "
                        f"VALUES ({placeholders})",
                        values,
                    )
                    inserted += max(affected, 0)
                totals[table] = (len(rows), inserted)
                print(f"  {table:22s} {len(rows):6d} rows, {inserted} inserted")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")

    print("")
    print("Migration complete.")
    print("The app now runs on MySQL. Delete the old SQLite file when you are "
          "confident everything carried over.")
    print(f"  source:     {source}")
    print(f"  target:     {settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}")
    print(f"  ran at:     {datetime.now(timezone.utc).isoformat(timespec='seconds')}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data" / "leadgen.db")