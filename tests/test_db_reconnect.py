"""Connection-manager behaviour: stale/dead connections, retry safety, cleanup,
concurrency, transactions, timeouts, health and the branding/landing/favicon
pages across a database outage.

Three layers:
  * fake-connection unit tests  - deterministic, exercise every retry decision;
  * integration tests           - the real PyMySQL driver against the isolated
                                  `*_test` database from conftest.py, using
                                  server-side KILL and a TCP proxy to inject
                                  network drops, blackholes and outages;
  * endpoint tests              - FastAPI routes before/during/after an outage.
"""

from __future__ import annotations

import gc
import logging
import socket
import threading
import time

import pymysql
import pytest

from app import db
from app.config import settings

SERVER_STATUS_IN_TRANS = 1


# ================================================================== fakes ==
class FakeCur:
    def __init__(self, conn):
        self.conn, self.lastrowid, self.rowcount, self.closed = conn, 0, 1, False
        conn.cursors.append(self)

    def execute(self, sql, params=None):
        self.conn.on_execute(sql)

    def fetchall(self):
        return [{"ok": 1}]

    def fetchone(self):
        return {"ok": 1}

    def close(self):
        self.closed = True
        if self.conn.close_raises:
            raise pymysql.err.InterfaceError(0, "")


class FakeConn:
    made: list["FakeConn"] = []
    failures: list[BaseException] = []     # consumed one per execute, any conn
    connect_failures: list[BaseException] = []
    executed: list[tuple[int, str]] = []

    def __init__(self):
        if FakeConn.connect_failures:
            raise FakeConn.connect_failures.pop(0)
        self.index = len(FakeConn.made)
        FakeConn.made.append(self)
        self.open, self.dead, self.close_raises = True, False, False
        self.server_status, self.cursors = 0, []
        self.pings, self.rollbacks, self.closed = 0, 0, False
        self.inflight, self.max_inflight, self.threads = 0, 0, set()

    def on_execute(self, sql):
        self.threads.add(threading.get_ident())
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            FakeConn.executed.append((self.index, sql))
            if self.dead:
                raise pymysql.err.InterfaceError(0, "")
            if FakeConn.failures:
                exc = FakeConn.failures.pop(0)
                if isinstance(exc, pymysql.err.OperationalError) and exc.args[0] in (2006, 2013):
                    self.open = False        # PyMySQL force-closes on these
                raise exc
        finally:
            self.inflight -= 1

    def cursor(self):
        return FakeCur(self)

    def ping(self, reconnect=False):
        self.pings += 1
        if self.dead or not self.open:
            raise pymysql.err.InterfaceError(0, "")

    def rollback(self):
        self.rollbacks += 1
        self.server_status &= ~SERVER_STATUS_IN_TRANS

    def close_open_cursors(self):
        for c in self.cursors:
            c.close() if not c.closed else None

    def close(self):
        self.open, self.closed = False, True

    def _force_close(self):
        self.open, self.closed = False, True


@pytest.fixture
def fake(monkeypatch):
    FakeConn.made, FakeConn.failures = [], []
    FakeConn.connect_failures, FakeConn.executed = [], []
    db.close_conn()
    monkeypatch.setattr(db, "_new_conn", FakeConn)
    yield FakeConn
    db.close_conn()


def lost(code=2013, msg="Lost connection to MySQL server during query"):
    return pymysql.err.OperationalError(code, msg)


def attempts(sql_prefix=None):
    return [s for _, s in FakeConn.executed if sql_prefix is None or s.startswith(sql_prefix)]


def prime():
    """Open a connection, then forget the priming query in the attempt log."""
    db.query("SELECT 1")
    FakeConn.executed.clear()


def age_idle(seconds=None):
    db._slot().used -= (seconds or db._IDLE_PING_SECONDS) + 1


# ============================================================ 1. reuse ====
def test_healthy_connection_is_reused(fake):
    for _ in range(5):
        assert db.query("SELECT 1") == [{"ok": 1}]
    assert len(fake.made) == 1
    assert fake.made[0].pings == 0          # no ping tax while busy


# ===================================================== 2/5. stale + swap ===
def test_stale_idle_connection_is_pinged_and_replaced_before_use(fake):
    db.query("SELECT 1")
    first = fake.made[0]
    first.dead = True
    age_idle()
    assert db.query("SELECT 1") == [{"ok": 1}]
    assert len(fake.made) == 2 and first.closed                # old one discarded
    assert attempts() == ["SELECT 1", "SELECT 1"]              # dead one never executed on


def test_healthy_idle_connection_survives_ping(fake):
    db.query("SELECT 1")
    age_idle()
    db.query("SELECT 1")
    assert len(fake.made) == 1 and fake.made[0].pings == 1


def test_closed_handle_is_replaced_without_ping(fake):
    prime()
    fake.made[0].open = False
    db.query("SELECT 1")
    assert len(fake.made) == 2 and fake.made[0].pings == 0


def test_connection_is_recycled_after_max_age(fake):
    db.query("SELECT 1")
    db._slot().created -= db._MAX_AGE_SECONDS + 1
    db.query("SELECT 1")
    assert len(fake.made) == 2 and fake.made[0].closed


def test_idle_sweep_closes_abandoned_handles(fake, monkeypatch):
    db.query("SELECT 1")
    slot = db._slot()
    slot.used -= db._MAX_IDLE_SECONDS + 1
    monkeypatch.setattr(db, "_last_sweep", 0.0)
    db._sweep(time.monotonic())
    assert slot.conn is None and fake.made[0].closed


def test_sweep_never_touches_a_checked_out_handle(fake, monkeypatch):
    with db.get_conn():
        slot = db._slot()
        slot.used -= db._MAX_IDLE_SECONDS + 1
        monkeypatch.setattr(db, "_last_sweep", 0.0)
        db._sweep(time.monotonic())
        assert slot.conn is not None and not fake.made[0].closed


# ===================================== 3/4. dropped by MySQL / by network ===
@pytest.mark.parametrize("exc", [lost(2013), lost(2006, "MySQL server has gone away"),
                                 lost(1927, "Connection was killed"),
                                 lost(1053, "Server shutdown in progress"),
                                 pymysql.err.InterfaceError(0, ""),
                                 BrokenPipeError(32, "Broken pipe"),
                                 ConnectionResetError(104, "reset")])
def test_read_survives_a_dropped_connection(fake, exc):
    prime()
    fake.failures.append(exc)
    assert db.query("SELECT 2") == [{"ok": 1}]
    assert attempts() == ["SELECT 2", "SELECT 2"]
    assert len(fake.made) == 2 and fake.made[0].closed


# ============================================================ 6. failed ===
def test_failed_reconnect_raises_once_and_recovers_later(fake):
    fake.connect_failures.append(lost(2003, "Can't connect to MySQL server"))
    with pytest.raises(pymysql.err.OperationalError) as ei:
        db.query("SELECT 1")
    assert ei.value.args[0] == 2003
    assert not fake.connect_failures                  # exactly one connect attempt
    assert db._slot().conn is None and db._slot().depth == 0 and not db._slot().busy
    assert db.query("SELECT 1") == [{"ok": 1}]        # next call heals


def test_failed_recovery_preserves_the_original_exception(fake):
    db.query("SELECT 1")
    original = lost(2013)
    fake.failures.append(original)
    fake.connect_failures.append(lost(2003, "Can't connect to MySQL server"))
    with pytest.raises(pymysql.err.OperationalError) as ei:
        db.query("SELECT 2")
    assert ei.value is original                       # not the retry's error
    assert ei.value.__cause__.args[0] == 2003          # ...which is kept as its cause


def test_retry_that_fails_with_sql_error_surfaces_that_error(fake):
    prime()
    fake.failures += [lost(2013), pymysql.err.ProgrammingError(1146, "no table")]
    with pytest.raises(pymysql.err.ProgrammingError):
        db.query("SELECT 2")


def test_retry_is_bounded_to_one(fake):
    prime()
    fake.failures += [lost(2013), lost(2013), lost(2013), lost(2013)]
    with pytest.raises(pymysql.err.OperationalError):
        db.query("SELECT 2")
    assert attempts("SELECT 2") == ["SELECT 2", "SELECT 2"]


# ============================================== 11/12. retry decisions ======
WRITES = ["INSERT INTO t VALUES (1)", "UPDATE t SET a=1", "DELETE FROM t",
          "REPLACE INTO t VALUES (1)", "INSERT IGNORE INTO t VALUES (1)",
          "START TRANSACTION", "COMMIT", "CALL p()",
          "WITH c AS (SELECT 1) UPDATE t SET a=1",
          "SELECT * FROM t FOR UPDATE", "SELECT GET_LOCK('x', 1)",
          "SELECT 1 INTO @v", "SELECT SLEEP(1)", "/* c */ UPDATE t SET a=1"]
READS = ["SELECT 1", "  select * from t", "SHOW TABLES", "DESCRIBE t",
         "EXPLAIN SELECT 1", "/* hint */ SELECT 1", "SELECT * FROM t WHERE a=%s"]


def test_statement_classification():
    assert all(db.is_read_only(s) for s in READS)
    assert not any(db.is_read_only(s) for s in WRITES)


@pytest.mark.parametrize("sql", READS)
@pytest.mark.parametrize("exc", [lost(2013), lost(2006, "gone away"), pymysql.err.InterfaceError(0, "")])
def test_reads_are_retried_once(fake, sql, exc):
    prime()
    fake.failures.append(exc)
    db.query(sql)
    assert len(attempts(sql)) == 2


@pytest.mark.parametrize("sql", WRITES)
@pytest.mark.parametrize("exc", [lost(2013), lost(2006, "MySQL server has gone away"),
                                 lost(1927, "killed"), BrokenPipeError(32, "pipe")])
def test_writes_are_never_retried_when_server_may_have_run_them(fake, sql, exc):
    prime()
    fake.failures.append(exc)
    with pytest.raises(type(exc)):
        db.execute(sql)
    assert len(attempts(sql)) == 1                     # exactly one attempt, no duplicate


@pytest.mark.parametrize("sql", ["INSERT INTO t VALUES (1)", "UPDATE t SET a=1", "DELETE FROM t"])
def test_writes_are_retried_only_when_provably_never_sent(fake, sql):
    prime()
    fake.failures.append(pymysql.err.InterfaceError(0, ""))   # PyMySQL: socket already gone
    db.execute(sql)
    assert len(attempts(sql)) == 2 and len(fake.made) == 2


def test_other_interface_errors_are_not_treated_as_never_sent(fake):
    prime()
    fake.failures.append(pymysql.err.InterfaceError("something else"))
    with pytest.raises(pymysql.err.InterfaceError):
        db.execute("UPDATE t SET a=1")
    assert len(attempts("UPDATE")) == 1


@pytest.mark.parametrize("exc", [
    pymysql.err.ProgrammingError(1064, "syntax error"),
    pymysql.err.IntegrityError(1062, "Duplicate entry"),
    pymysql.err.OperationalError(1045, "Access denied for user"),
    pymysql.err.OperationalError(1213, "Deadlock found"),
    pymysql.err.OperationalError(1205, "Lock wait timeout"),
    pymysql.err.DataError(1406, "Data too long"),
    ValueError("application bug"),
])
@pytest.mark.parametrize("sql", ["SELECT 1", "UPDATE t SET a=1"])
def test_ordinary_errors_are_never_retried_and_keep_the_connection(fake, exc, sql):
    prime()
    fake.failures.append(exc)
    with pytest.raises(type(exc)):
        (db.query if sql.startswith("SELECT") else db.execute)(sql)
    assert len(attempts(sql)) == 1 and len(fake.made) == 1 and not fake.made[0].closed


def test_read_timeout_is_not_retried(fake):
    prime()
    fake.failures.append(lost(2013, "Lost connection to MySQL server during query (timed out)"))
    with pytest.raises(pymysql.err.OperationalError):
        db.query("SELECT sleepy()")
    assert len(attempts("SELECT sleepy")) == 1
    assert db._slot().conn is None                      # and the handle was discarded


def test_nested_use_inside_a_transaction_is_never_retried(fake):
    with db.get_conn():
        fake.failures.append(lost(2013))
        with pytest.raises(pymysql.err.OperationalError):
            db.query("SELECT 1")                        # would silently leave the txn
    assert len(attempts("SELECT 1")) == 1 and len(fake.made) == 1
    assert db._slot().conn is None                      # outer release discarded it


# =================================================== 8. cursor cleanup ======
def test_cursors_are_closed_on_success_and_on_error(fake):
    prime()
    fake.failures.append(pymysql.err.ProgrammingError(1064, "x"))
    with pytest.raises(pymysql.err.ProgrammingError):
        db.query("BAD")
    assert fake.made[0].cursors and all(c.closed for c in fake.made[0].cursors)


def test_cursor_close_failure_never_masks_the_real_error(fake):
    prime()
    fake.made[0].close_raises = True
    fake.failures.append(pymysql.err.IntegrityError(1062, "dup"))
    with pytest.raises(pymysql.err.IntegrityError):
        db.execute("INSERT INTO t VALUES (1)")


def test_forgotten_sqlite_style_cursors_are_closed_on_release(monkeypatch):
    """Conn.execute() hands back a cursor nobody closes; release must."""
    closed = []

    class Cur:
        def close(self):
            closed.append(self)

    conn = db.Conn.__new__(db.Conn)
    conn.cursor = lambda: Cur()
    c = conn._track(conn.cursor())
    conn.close_open_cursors()
    assert closed == [c]


# ============================================ 9. cleanup after exceptions ===
def test_ordinary_exception_keeps_the_handle(fake):
    with pytest.raises(ValueError):
        with db.get_conn():
            raise ValueError("app bug")
    assert db._slot().conn is fake.made[0] and not fake.made[0].closed
    assert db._slot().depth == 0 and not db._slot().busy


@pytest.mark.parametrize("exc", [lost(2013), KeyboardInterrupt(), SystemExit(1),
                                 pymysql.err.InternalError("Packet sequence number wrong")])
def test_unsafe_exceptions_discard_the_handle(fake, exc):
    with pytest.raises(type(exc)):
        with db.get_conn():
            raise exc
    assert db._slot().conn is None and fake.made[0].closed
    assert db._slot().depth == 0 and not db._slot().busy
    db.query("SELECT 1")
    assert len(fake.made) == 2


# ===================================================== 10/11. transactions ==
def test_transaction_left_open_is_rolled_back_on_release(fake, caplog):
    with caplog.at_level(logging.WARNING, logger="leadgen.db"):
        with db.get_conn() as conn:
            conn.server_status |= SERVER_STATUS_IN_TRANS
    assert fake.made[0].rollbacks == 1
    assert "rolling back" in caplog.text


def test_open_transaction_rolled_back_when_body_raises(fake):
    with pytest.raises(ValueError):
        with db.get_conn() as conn:
            conn.server_status |= SERVER_STATUS_IN_TRANS
            raise ValueError
    assert fake.made[0].rollbacks == 1


def test_failed_rollback_discards_the_handle(fake):
    with db.get_conn() as conn:
        conn.server_status |= SERVER_STATUS_IN_TRANS
        conn.rollback = lambda: (_ for _ in ()).throw(lost(2006, "gone"))
    assert db._slot().conn is None


def test_safe_rollback_does_not_mask_the_original_error(fake):
    """The pattern in quota/search/automation: `except: safe_rollback; raise`."""
    original = lost(2013)
    with pytest.raises(pymysql.err.OperationalError) as ei:
        with db.get_conn() as conn:
            conn.rollback = lambda: (_ for _ in ()).throw(pymysql.err.InterfaceError(0, ""))
            try:
                raise original
            except Exception:
                db.safe_rollback(conn)
                raise
    assert ei.value is original


# =============================================================== 7. threads ==
def test_connections_are_never_shared_between_threads(fake):
    errors = []

    def worker():
        try:
            for i in range(60):
                db.query("SELECT 1")
                if i % 10 == 0:
                    db.execute("UPDATE t SET a=1")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert len(fake.made) == 16                                  # one per thread, no churn
    assert all(len(c.threads) == 1 and c.max_inflight == 1 for c in fake.made)


def test_thread_exit_releases_its_connection(fake):
    t = threading.Thread(target=lambda: db.query("SELECT 1"))
    t.start()
    t.join()
    gc.collect()
    assert not any(s.conn for s in list(db._registry) if s is not db._slot() and s.conn is fake.made[0])


# ============================================================ config ========
def test_real_connections_carry_timeouts():
    db.close_conn()
    with db.get_conn() as conn:
        assert conn._read_timeout == settings.mysql_read_timeout > 0
        assert conn._write_timeout == settings.mysql_write_timeout > 0
        assert conn.connect_timeout == settings.mysql_connect_timeout > 0


# ===================================================== integration helpers ==
class Proxy:
    """TCP proxy in front of the test MySQL so tests can sever, blackhole or
    take down 'the network' without touching the server."""

    def __init__(self):
        self.mode, self.pairs, self.lock = "pass", [], threading.Lock()
        self.srv = None
        self.port = 0
        self.listen(0)

    def listen(self, port):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.listen(32)
        self.srv, self.port = s, s.getsockname()[1]
        threading.Thread(target=self._accept, args=(s,), daemon=True).start()

    def _accept(self, srv):
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            try:
                u = socket.create_connection((settings.mysql_host, self._upstream), timeout=5)
            except OSError:
                c.close()
                continue
            with self.lock:
                self.pairs.append((c, u))
            for a, b in ((c, u), (u, c)):
                threading.Thread(target=self._pump, args=(a, b), daemon=True).start()

    def _pump(self, a, b):
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                if self.mode == "blackhole":
                    continue
                b.sendall(data)
        except OSError:
            pass
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def sever(self):
        with self.lock:
            pairs, self.pairs = self.pairs, []
        for c, u in pairs:
            for s in (c, u):
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
                    s.close()
                except OSError:
                    pass

    def down(self):
        try:
            self.srv.shutdown(socket.SHUT_RDWR)      # wakes the blocked accept()
        except OSError:
            pass
        self.srv.close()
        self.sever()

    def up(self):
        self.listen(self.port)

    _upstream = 0


@pytest.fixture
def proxy(monkeypatch):
    p = Proxy()
    p._upstream = settings.mysql_port
    db.close_conn()
    monkeypatch.setattr(settings, "mysql_host", "127.0.0.1")
    real_port = settings.mysql_port
    monkeypatch.setattr(settings, "mysql_port", p.port)
    p._upstream = real_port
    yield p
    p.mode = "pass"
    db.close_conn()
    try:
        p.srv.close()
    except OSError:
        pass
    p.sever()


def conn_id():
    return db.scalar("SELECT CONNECTION_ID()")


def kill_my_connection():
    """Server-side kill of this thread's connection, from a second connection."""
    victim = conn_id()
    admin = pymysql.connect(host=settings.mysql_host, port=settings.mysql_port,
                            user=settings.mysql_user, password=settings.mysql_password,
                            database=settings.mysql_database)
    try:
        with admin.cursor() as cur:
            cur.execute(f"KILL CONNECTION {int(victim)}")
    finally:
        admin.close()
    time.sleep(0.2)
    return victim


@pytest.fixture
def scratch():
    key = f"_t_{time.monotonic_ns()}"
    yield key
    db.execute("DELETE FROM app_settings WHERE setting_key LIKE '\\_t\\_%'")


def count(key):
    return db.scalar("SELECT COUNT(*) FROM app_settings WHERE setting_key = ?", (key,))


def insert(key, value="v"):
    return db.execute("INSERT INTO app_settings(setting_key, value) VALUES(?, ?)", (key, value))


# ============================================ integration: real driver ======
def test_real_connection_is_reused_across_queries():
    db.close_conn()
    ids = {conn_id() for _ in range(10)}
    assert len(ids) == 1


def test_real_server_kill_then_read_recovers_immediately():
    db.close_conn()
    old = kill_my_connection() if conn_id() else None
    assert conn_id() != old                              # retried on a fresh connection


def test_real_server_kill_then_idle_read_recovers_via_ping():
    db.close_conn()
    old = conn_id()
    kill_my_connection()
    age_idle()
    assert conn_id() != old


def test_real_server_kill_then_write_is_not_retried_and_not_duplicated(scratch):
    db.close_conn()
    conn_id()
    kill_my_connection()
    with pytest.raises(pymysql.err.OperationalError):
        insert(scratch)                                   # surfaced, not silently replayed
    assert count(scratch) == 0
    insert(scratch)                                       # next write heals and lands once
    assert count(scratch) == 1


def test_real_server_kill_idle_write_succeeds_exactly_once(scratch):
    db.close_conn()
    conn_id()
    kill_my_connection()
    age_idle()                                            # ping detects it BEFORE sending
    insert(scratch)
    assert count(scratch) == 1


def test_real_network_drop(proxy, scratch):
    first = conn_id()
    proxy.sever()
    time.sleep(0.1)
    assert conn_id() != first                             # read healed
    proxy.sever()
    time.sleep(0.1)
    with pytest.raises(pymysql.err.OperationalError):
        insert(scratch)
    assert count(scratch) == 0
    insert(scratch)
    assert count(scratch) == 1


def test_real_outage_and_recovery_without_restart(proxy):
    """MySQL (or the network) disappears entirely, then returns."""
    conn_id()
    proxy.down()
    t0 = time.monotonic()
    for _ in range(3):
        with pytest.raises(pymysql.err.OperationalError):
            db.query("SELECT 1")
    assert time.monotonic() - t0 < 5                      # fails fast, no pile-up
    assert db._slot().conn is None and not db._slot().busy
    proxy.up()
    assert db.query("SELECT 1 AS ok") == [{"ok": 1}]      # same process, no restart


def test_real_query_timeout(proxy, monkeypatch):
    monkeypatch.setattr(settings, "mysql_read_timeout", 1)
    db.close_conn()
    conn_id()
    proxy.mode = "blackhole"
    t0 = time.monotonic()
    with pytest.raises(pymysql.err.OperationalError, match="timed out|Lost connection"):
        db.query("SELECT 1")
    elapsed = time.monotonic() - t0
    assert 0.8 < elapsed < 2.5                            # one timeout, not two (no retry)
    assert db._slot().conn is None
    proxy.mode = "pass"
    assert db.scalar("SELECT 1") == 1


def test_real_transaction_rollback_on_exception(scratch):
    db.close_conn()
    cid = conn_id()
    with pytest.raises(RuntimeError):
        with db.get_conn() as conn:
            conn.execute("START TRANSACTION")
            conn.execute("INSERT INTO app_settings(setting_key, value) VALUES(%s,'x')", (scratch,))
            raise RuntimeError("boom")
    assert count(scratch) == 0
    assert conn_id() == cid                               # handle reused, transaction gone
    assert not (db._slot().conn.server_status & SERVER_STATUS_IN_TRANS)


def test_real_forgotten_transaction_is_rolled_back(scratch, caplog):
    with caplog.at_level(logging.WARNING, logger="leadgen.db"):
        with db.get_conn() as conn:
            conn.execute("START TRANSACTION")
            conn.execute("INSERT INTO app_settings(setting_key, value) VALUES(%s,'x')", (scratch,))
    assert count(scratch) == 0 and "rolling back" in caplog.text


def test_real_transaction_killed_midway_keeps_original_error(scratch):
    db.close_conn()
    with pytest.raises(pymysql.err.OperationalError) as ei:
        with db.get_conn() as conn:
            conn.execute("START TRANSACTION")
            conn.execute("INSERT INTO app_settings(setting_key, value) VALUES(%s,'x')", (scratch,))
            kill_my_connection()
            try:
                conn.execute("SELECT 1")
            except Exception:
                db.safe_rollback(conn)
                raise
    assert ei.value.args[0] in (2006, 2013) or isinstance(ei.value, pymysql.err.InterfaceError)
    assert count(scratch) == 0                            # server discarded the txn


def test_real_forgotten_cursors_are_closed_on_release():
    db.close_conn()
    with db.get_conn() as conn:
        held = [conn.execute("SELECT 1") for _ in range(5)]   # sqlite-style, never closed
        assert all(c.connection is not None for c in held)
    assert all(c.connection is None for c in held)             # PyMySQL's "closed" marker
    assert db._slot().conn is not None                         # the handle itself is kept


def test_real_concurrent_requests_are_isolated_and_leak_free(scratch):
    def threads_connected():
        return int(db.query_one("SHOW STATUS LIKE 'Threads_connected'")["Value"])

    gc.collect()
    time.sleep(0.3)
    baseline = threads_connected()
    errors, ids = [], {}

    def worker(n):
        try:
            mine = set()
            for i in range(40):
                mine.add(conn_id())
                insert(f"{scratch}_{n}_{i}")
                assert db.scalar("SELECT COUNT(*) FROM app_settings WHERE setting_key = ?",
                                 (f"{scratch}_{n}_{i}",)) == 1
            ids[n] = mine
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    ts = [threading.Thread(target=worker, args=(n,)) for n in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    assert all(len(v) == 1 for v in ids.values())          # a thread never changes handle
    assert len(set().union(*ids.values())) == 12           # and never shares one
    assert db.scalar("SELECT COUNT(*) FROM app_settings WHERE setting_key LIKE ?",
                     (f"{scratch}\\_%",)) == 12 * 40       # no lost / duplicated writes
    for _ in range(20):                                    # dead threads' handles are freed
        gc.collect()
        if threads_connected() <= baseline:
            break
        time.sleep(0.1)
    assert threads_connected() <= baseline


# ====================================================== endpoint behaviour ==
@pytest.fixture
def outage(monkeypatch):
    """Make 'MySQL' unreachable for every thread, then let the test restore it."""
    state = {"down": False}
    real = db._new_conn

    def new_conn():
        if state["down"]:
            raise pymysql.err.OperationalError(
                2003, f"Can't connect to MySQL server on '{settings.mysql_host}' (password {settings.mysql_password})")
        return real()

    monkeypatch.setattr(db, "_new_conn", new_conn)

    def go_down():
        state["down"] = True
        for slot in list(db._registry):                    # every handle dies with the server
            if slot.conn is not None and not slot.busy:
                slot.conn._force_close()

    state["go_down"], state["go_up"] = go_down, lambda: state.update(down=False)
    return state


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_live_does_not_touch_the_database(client, outage):
    outage["go_down"]()
    r = client.get("/health/live")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_ready_reports_ok_then_503_then_ok_again(client, outage, caplog):
    assert client.get("/health/ready").status_code == 200
    outage["go_down"]()
    with caplog.at_level(logging.ERROR):
        r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["mysql"] == "error"
    assert "readiness: database check failed" in caplog.text and "2003" in caplog.text
    outage["go_up"]()
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_ready_never_leaks_credentials_or_connection_details(client, outage):
    outage["go_down"]()
    body = client.get("/health/ready").text + client.get("/health").text
    for secret in (settings.mysql_password, settings.mysql_user, settings.mysql_host,
                   str(settings.mysql_port), "pymysql", "Traceback"):
        assert secret not in body


def test_ready_heals_a_stale_connection_instead_of_failing(client):
    assert client.get("/health/ready").status_code == 200
    for slot in list(db._registry):
        if slot.conn is not None:
            slot.conn._force_close()                       # what a restart leaves behind
    assert client.get("/health/ready").status_code == 200


def test_health_checks_do_not_leak_connections(client):
    client.get("/health/ready")
    before = db.pool_stats()
    for _ in range(60):
        assert client.get("/health/ready").status_code == 200
        assert client.get("/health").status_code == 200
        assert client.get("/health/live").status_code == 200
    after = db.pool_stats()
    assert after["open"] <= before["open"] + 1 and after["busy"] == 0


def test_health_endpoint_503_when_db_down(client, outage):
    outage["go_down"]()
    assert client.get("/health").status_code == 503
    outage["go_up"]()
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("path", ["/landing", "/favicon.ico", "/api/branding/theme.css"])
def test_branding_pages_survive_an_outage_without_restart(client, outage, path):
    first = client.get(path, follow_redirects=False)
    assert first.status_code in (200, 302)
    outage["go_down"]()
    during = client.get(path, follow_redirects=False)
    assert during.status_code >= 500                       # honest failure, no secrets
    assert settings.mysql_password not in during.text
    outage["go_up"]()
    after = client.get(path, follow_redirects=False)
    assert after.status_code == first.status_code           # recovered in-process
    if path == "/landing":
        assert "{{" not in after.text


def test_branding_values_come_back_after_recovery(client, outage):
    from app.branding import get_branding
    expected = get_branding()["brand_name"]
    outage["go_down"]()
    with pytest.raises(pymysql.err.OperationalError):
        get_branding()
    outage["go_up"]()
    assert get_branding()["brand_name"] == expected
