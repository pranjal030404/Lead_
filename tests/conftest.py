"""Pytest setup for a MySQL test database.

Tests MUST run against a dedicated test database - never the production one.
Everything in this file is forced to an isolated, disposable target:

  * MYSQL_DATABASE is pinned to a name that ends in `_test`.
  * All application tables are dropped at session start, then recreated by
    init_db(), so a run is deterministic however many times it ran before.

The MySQL server itself is not created here. The dev setup uses a local
instance on 127.0.0.1:3306 with user leadgen / password leadgenpass owning the
`leadgen_test` database (see the "Database setup" section of the README).
Override the MYSQL_HOST/PORT/USER/PASSWORD env vars to point somewhere else -
MYSQL_DATABASE is still forced to a `_test` name.

Note: config.py reads these env vars at import time, so they must be set before
any `from app import ...` line runs - that is why they live at the top of this
file (pytest imports conftest before any test module).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("PROVIDER", "mock")
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("ADMIN_USER", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "pytest-admin")
os.environ.setdefault("SECRET_KEY", "pytest-secret")
os.environ.setdefault("MYSQL_HOST", "127.0.0.1")
os.environ.setdefault("MYSQL_PORT", "3306")
os.environ.setdefault("MYSQL_USER", "leadgen")
os.environ.setdefault("MYSQL_PASSWORD", "leadgenpass")
# Pinned before `.env` is loaded (app.config calls load_dotenv) so the tests are
# never pointed at the dev/`leadgen` database by accident.
os.environ.setdefault("MYSQL_DATABASE", "leadgen_test")

import pytest  # noqa: E402

TABLES = [
    "leads", "interactions", "searches", "search_coverage", "targets",
    "templates", "pipeline_stages", "suppression_list", "approval_queue",
    "api_usage", "failed_jobs", "automation_log", "app_settings",
    "scheduler_lease", "alerts_sent", "users", "plans", "user_subscriptions",
]


@pytest.fixture(scope="session", autouse=True)
def _mysql_test_db():
    database = os.environ.get("MYSQL_DATABASE", "").strip()
    if not database.endswith("_test"):
        raise SystemExit(
            "Refusing to run tests against a non-test database "
            f"(MYSQL_DATABASE='{database}'). Set MYSQL_DATABASE=leadgen_test "
            "or similar."
        )

    from app import config as _config
    from app.db import close_conn, init_db

    _tmp = tempfile.mkdtemp(prefix="leadgen-test-")
    _config.settings.backup_dir = Path(_tmp) / "backups"
    _config.settings.backup_dir.mkdir(exist_ok=True)
    _config.settings.data_dir = Path(_tmp)

    import pymysql  # noqa: PLC0415

    conn = pymysql.connect(
        host=_config.settings.mysql_host,
        port=_config.settings.mysql_port,
        user=_config.settings.mysql_user,
        password=_config.settings.mysql_password,
        database=_config.settings.mysql_database,
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in TABLES:
                cur.execute(f"DROP TABLE IF EXISTS `{table}`")
    finally:
        conn.close()

    init_db()
    yield
    close_conn()