"""Phase 2: workspace isolation.

Two tenants, A and B. Everything A owns must be invisible and immutable to B
through every endpoint family, and background work must stay inside its
workspace. A lead in another workspace must look exactly like a missing one
(404), so ids can't be probed.
"""

from __future__ import annotations

import pymysql
import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import (Conn, MIGRATIONS, NoWorkspace, current_ws, execute, query, query_one,
                    run_migrations, utcnow, workspace_context)
from app.main import app
from app.outreach import (generate_followups, is_suppressed, queue_message, send_approved,
                          suppress)
from app.search_service import find_cached_search, run_search
from app.security import SESSION_COOKIE, hash_password, issue_token
from app.tenancy import add_member, create_workspace, memberships


def _user(username, role="admin"):
    execute(
        "REPLACE INTO users(username, password_hash, full_name, role, is_active, created_at) "
        "VALUES(?,?,?,?,1,?)", (username, hash_password("pw-" + username), username, role,
                                utcnow()))
    return query_one("SELECT id FROM users WHERE username = ?", (username,))["id"]


def _client(username):
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, issue_token(username))
    return c


def _lead(workspace_id, name="Acme Dental", **extra):
    cols = {"workspace_id": workspace_id, "business_name": name, "city": "Delhi",
            "niche": "dentists", "lead_score": 5, "lead_priority": "WARM",
            "status": "NEW", "phone": extra.pop("phone", None),
            "created_at": utcnow(), "updated_at": utcnow(), **extra}
    return execute(
        f"INSERT INTO leads({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})",
        tuple(cols.values()))


@pytest.fixture(scope="module")
def tenants():
    ws_a = create_workspace("Tenant A")
    ws_b = create_workspace("Tenant B")
    alice, bob = _user("ten_alice"), _user("ten_bob")
    add_member(ws_a, alice, "owner")
    add_member(ws_b, bob, "owner")
    return {"a": ws_a, "b": ws_b, "alice": alice, "bob": bob,
            "ca": _client("ten_alice"), "cb": _client("ten_bob")}


# ------------------------------------------------------------------ leads --

def test_lead_endpoints_do_not_cross_tenants(tenants):
    lead_id = _lead(tenants["a"], "Secret Clinic", phone="+911111111111")
    execute("INSERT INTO interactions(workspace_id, lead_id, type, date, created_at) "
            "VALUES(?,?,'call',?,?)", (tenants["a"], lead_id, utcnow(), utcnow()))
    ca, cb = tenants["ca"], tenants["cb"]

    assert ca.get(f"/api/leads/{lead_id}").status_code == 200
    listed = cb.get("/api/leads").json()
    assert listed["total"] == 0 and listed["leads"] == []
    assert cb.get("/api/leads", params={"q": "Secret"}).json()["total"] == 0

    assert cb.get(f"/api/leads/{lead_id}").status_code == 404
    assert cb.patch(f"/api/leads/{lead_id}", json={"notes": "hacked"}).status_code == 404
    assert cb.delete(f"/api/leads/{lead_id}").status_code == 404
    assert cb.post(f"/api/leads/{lead_id}/enrich").status_code == 404
    assert cb.get(f"/api/leads/{lead_id}/interactions").status_code == 404
    assert cb.post(f"/api/leads/{lead_id}/interactions",
                   json={"type": "call"}).status_code == 404
    assert cb.post(f"/api/leads/{lead_id}/message",
                   params={"template": "cold_email_first"}).status_code == 404

    cb.post("/api/leads/bulk-status", json={"ids": [lead_id], "status": "LOST"})
    cb.post("/api/leads/bulk-tag", json={"ids": [lead_id], "tag": "pwned"})
    row = query_one("SELECT status, tags, notes FROM leads WHERE id = ?", (lead_id,))
    assert row["status"] == "NEW" and not row["tags"] and not row["notes"]

    assert "Secret Clinic" not in cb.get("/api/leads/export").text
    assert "Secret Clinic" in ca.get("/api/leads/export").text
    assert cb.get("/api/leads/facets").json()["cities"] == []
    assert query_one("SELECT COUNT(*) AS n FROM leads WHERE id = ?", (lead_id,))["n"] == 1


def test_dashboard_pipeline_and_map_are_scoped(tenants):
    _lead(tenants["a"], "Mapped Co", lat=28.6, lng=77.2, follow_up_date="2000-01-01")
    ca, cb = tenants["ca"], tenants["cb"]
    assert cb.get("/api/stats").json()["total_leads"] == 0
    assert ca.get("/api/stats").json()["total_leads"] >= 1
    assert cb.get("/api/geo/map").json()["count"] == 0
    assert ca.get("/api/geo/map").json()["count"] >= 1
    assert all(s["count"] == 0 and s["leads"] == [] for s in cb.get("/api/pipeline").json())
    assert cb.get("/api/follow-ups/overdue").json() == []
    assert cb.get("/api/follow-ups/today").json() == []
    assert cb.get("/api/stats/weekly").json()["leads_found"] == 0
    assert cb.get("/api/stats/conversions").json()["results_seen"] == 0
    assert cb.get("/api/automation/status").json()["today"]["leads_found"] == 0


def test_same_place_id_allowed_in_two_workspaces(tenants):
    _lead(tenants["a"], "Chain A", place_id="shared-place")
    _lead(tenants["b"], "Chain B", place_id="shared-place")  # no IntegrityError
    with pytest.raises(pymysql.err.IntegrityError):
        _lead(tenants["a"], "Chain A dup", place_id="shared-place")


def test_insert_without_workspace_fails_loudly():
    with pytest.raises(pymysql.err.OperationalError):
        execute("INSERT INTO leads(business_name) VALUES('orphan')")


# ----------------------------------------------------- per-user ownership --

@pytest.fixture(scope="module")
def shared_team():
    """One workspace, one admin and two regular members - the shape that makes
    per-user lead separation observable."""
    ws = create_workspace("Shared Team")
    owner = _user("own_boss", "admin")
    worker = _user("own_worker", "user")
    peer = _user("own_peer", "user")
    add_member(ws, owner, "owner")
    add_member(ws, worker, "member")
    add_member(ws, peer, "member")
    return {"ws": ws, "owner": owner, "worker": worker, "peer": peer,
            "cowner": _client("own_boss"), "cworker": _client("own_worker"),
            "cpeer": _client("own_peer")}


def test_leads_are_private_to_their_owner(shared_team):
    s = shared_team
    mine = _lead(s["ws"], "Worker Dental", owner_user_id=s["worker"])
    theirs = _lead(s["ws"], "Peer Dental", owner_user_id=s["peer"])

    worker_names = {x["business_name"] for x in s["cworker"].get("/api/leads").json()["leads"]}
    assert "Worker Dental" in worker_names and "Peer Dental" not in worker_names

    assert s["cworker"].get(f"/api/leads/{mine}").status_code == 200
    assert s["cworker"].get(f"/api/leads/{theirs}").status_code == 404
    assert s["cpeer"].get(f"/api/leads/{mine}").status_code == 404

    # A peer's lead is unreadable AND immutable, through any door.
    assert s["cworker"].patch(f"/api/leads/{theirs}",
                              json={"notes": "hacked"}).status_code == 404
    s["cworker"].post("/api/leads/bulk-status", json={"ids": [theirs], "status": "LOST"})
    s["cworker"].post("/api/leads/bulk-tag", json={"ids": [theirs], "tag": "pwned"})
    row = query_one("SELECT status, notes, tags FROM leads WHERE id = ?", (theirs,))
    assert row["status"] != "LOST" and not row["notes"] and not row["tags"]

    assert "Peer Dental" not in s["cworker"].get("/api/leads/export").text
    assert "Worker Dental" in s["cworker"].get("/api/leads/export").text

    assert s["cworker"].get("/api/stats").json()["total_leads"] == 1
    assert s["cworker"].get(f"/api/leads/{theirs}/interactions").status_code == 404


def test_workspace_admin_sees_every_lead(shared_team):
    s = shared_team
    _lead(s["ws"], "Worker Two", owner_user_id=s["worker"])
    _lead(s["ws"], "Peer Two", owner_user_id=s["peer"])
    names = {x["business_name"] for x in s["cowner"].get("/api/leads").json()["leads"]}
    assert {"Worker Two", "Peer Two"} <= names
    assert s["cowner"].get("/api/stats").json()["total_leads"] >= 2


def test_same_place_id_allowed_for_two_owners_in_one_workspace(shared_team):
    s = shared_team
    _lead(s["ws"], "Dup Worker", place_id="dupe-place", owner_user_id=s["worker"])
    _lead(s["ws"], "Dup Peer", place_id="dupe-place", owner_user_id=s["peer"])  # allowed
    with pytest.raises(pymysql.err.IntegrityError):
        _lead(s["ws"], "Dup Worker 2", place_id="dupe-place", owner_user_id=s["worker"])


# -------------------------------------------------- templates / suppression --

def test_templates_and_suppression_are_per_workspace(tenants):
    ca, cb = tenants["ca"], tenants["cb"]
    assert ca.post("/api/templates", json={"name": "only_a", "body": "hi {business_name}"}
                   ).status_code == 200
    assert "only_a" not in [t["name"] for t in cb.get("/api/templates").json()]
    a_tpl = query_one("SELECT id FROM templates WHERE name = 'only_a'")["id"]
    cb.delete(f"/api/templates/{a_tpl}")
    assert query_one("SELECT id FROM templates WHERE id = ?", (a_tpl,)) is not None
    # same name is a different row in B
    assert cb.post("/api/templates", json={"name": "only_a", "body": "B version"}).status_code == 200
    assert query_one("SELECT body FROM templates WHERE id = ?", (a_tpl,))["body"].startswith("hi")

    ca.post("/api/suppression", json={"value": "optout@x.com"})
    assert [r["value"] for r in ca.get("/api/suppression").json()] == ["optout@x.com"]
    assert cb.get("/api/suppression").json() == []
    with workspace_context(tenants["a"]):
        assert is_suppressed("optout@x.com")
    with workspace_context(tenants["b"]):
        assert not is_suppressed("optout@x.com")
    cb.delete("/api/suppression", params={"value": "optout@x.com"})
    assert len(ca.get("/api/suppression").json()) == 1


def test_new_workspace_gets_seeded_templates(tenants):
    with workspace_context(tenants["b"]):
        names = {t["name"] for t in query("SELECT name FROM templates WHERE workspace_id = ?",
                                          (current_ws(),))}
    assert "cold_email_first" in names


def test_identity_settings_are_per_workspace(tenants):
    tenants["ca"].post("/api/identity", json={"company_name": "A Studio"})
    assert tenants["ca"].get("/api/identity").json()["company_name"] == "A Studio"
    assert tenants["cb"].get("/api/identity").json()["company_name"] != "A Studio"


# --------------------------------------------------------------- approvals --

def test_approval_queue_is_isolated_and_sending_is_per_workspace(tenants):
    lead_id = _lead(tenants["a"], "Queue Me", email="q@example.org", email_valid=1)
    with workspace_context(tenants["a"]):
        lead = query_one("SELECT * FROM leads WHERE id = ?", (lead_id,))
        queue_id = queue_message(lead, "cold_email_first", "manual")
    assert queue_id
    ca, cb = tenants["ca"], tenants["cb"]
    assert len(ca.get("/api/approvals").json()) >= 1
    assert cb.get("/api/approvals").json() == []
    assert cb.post(f"/api/approvals/{queue_id}/approve").status_code == 404
    assert cb.patch(f"/api/approvals/{queue_id}", json={"body": "x"}).status_code == 404
    cb.post("/api/approvals/approve-all", json={"ids": [queue_id]})
    assert query_one("SELECT status FROM approval_queue WHERE id = ?", (queue_id,))["status"] \
        == "PENDING"
    assert cb.post("/api/approvals/queue", json={"lead_id": lead_id,
                   "template": "cold_email_first"}).status_code == 404

    assert ca.post(f"/api/approvals/{queue_id}/approve").status_code == 200
    # B pressing "send" must not send A's approved mail ...
    assert cb.post("/api/approvals/send").json()["sent"] == 0
    assert query_one("SELECT status FROM approval_queue WHERE id = ?", (queue_id,))["status"] \
        == "APPROVED"
    # ... and a scheduler-style pass for workspace B doesn't either.
    assert send_approved(workspace_id=tenants["b"])["sent"] == 0
    assert send_approved(workspace_id=tenants["a"])["sent"] == 1
    assert query_one("SELECT workspace_id FROM interactions WHERE lead_id = ?",
                     (lead_id,))["workspace_id"] == tenants["a"]


def test_queue_message_refuses_foreign_lead(tenants):
    lead_id = _lead(tenants["a"], "Foreign", email="f@example.org")
    lead = query_one("SELECT * FROM leads WHERE id = ?", (lead_id,))
    with workspace_context(tenants["b"]):
        with pytest.raises(PermissionError):
            queue_message(lead, "cold_email_first", "manual")


def test_followup_generation_only_touches_its_workspace(tenants):
    old = "2000-01-01T00:00:00+00:00"
    la = _lead(tenants["a"], "Stale A", status="CONTACTED", last_contacted_date=old)
    lb = _lead(tenants["b"], "Stale B", status="CONTACTED", last_contacted_date=old)
    with workspace_context(tenants["a"]):
        generate_followups()
    assert query_one("SELECT status FROM leads WHERE id = ?", (la,))["status"] == "LOST"
    assert query_one("SELECT status FROM leads WHERE id = ?", (lb,))["status"] == "CONTACTED"


# ----------------------------------------------------- search / targets ----

def test_search_history_coverage_cache_and_dedup_are_per_workspace(tenants):
    with workspace_context(tenants["a"]):
        res = run_search(niche="florists", city="Isoville", max_results=5,
                         provider_name="mock", enrich=False)
        assert res["status"] == "COMPLETED" and res["new_leads"] > 0
        sid = res["search_id"]
        assert find_cached_search("florists", "Isoville", provider_name="mock")
    ca, cb = tenants["ca"], tenants["cb"]
    assert ca.get(f"/api/search/{sid}/status").status_code == 200
    assert cb.get(f"/api/search/{sid}/status").status_code == 404
    assert sid in [r["id"] for r in ca.get("/api/search/history").json()]
    assert sid not in [r["id"] for r in cb.get("/api/search/history").json()]
    assert "Isoville" in ca.get("/api/coverage").json()["cities"]
    assert "Isoville" not in cb.get("/api/coverage").json()["cities"]
    assert cb.get("/api/coverage/Isoville").json() == []
    assert cb.post(f"/api/search/{sid}/cancel").json() == {"cancelled": False}

    with workspace_context(tenants["b"]):
        # No cache sharing, and B's identical search finds its own new leads
        # instead of being deduplicated against A's.
        assert find_cached_search("florists", "Isoville", provider_name="mock") is None
        res_b = run_search(niche="florists", city="Isoville", max_results=5,
                           provider_name="mock", enrich=False)
        assert res_b["new_leads"] == res["new_leads"] and res_b["duplicates_skipped"] == 0
    leads_b = query("SELECT DISTINCT workspace_id FROM leads WHERE search_id = ?",
                    (res_b["search_id"],))
    assert [r["workspace_id"] for r in leads_b] == [tenants["b"]]


def test_targets_are_isolated(tenants):
    ca, cb = tenants["ca"], tenants["cb"]
    t = ca.post("/api/targets", json={"niche": "gyms", "city": "Pune"}).json()
    assert cb.get("/api/targets").json() == []
    assert cb.patch(f"/api/targets/{t['id']}", params={"is_active": False}).status_code == 404
    cb.delete(f"/api/targets/{t['id']}")
    assert cb.post(f"/api/targets/{t['id']}/search-now").status_code == 404
    assert query_one("SELECT is_active FROM targets WHERE id = ?", (t["id"],))["is_active"] == 1
    # B can add the identical target independently
    assert cb.post("/api/targets", json={"niche": "gyms", "city": "Pune"}).json()["id"] != t["id"]


# ------------------------------------------------------ request resolution --

def test_workspace_header_must_be_a_membership(tenants):
    ca = tenants["ca"]
    assert ca.get("/api/leads", headers={"X-Workspace-Id": str(tenants["b"])}).status_code == 403
    assert ca.get("/api/leads", headers={"X-Workspace-Id": "abc"}).status_code == 403
    assert ca.get("/api/leads", headers={"X-Workspace-Id": str(tenants["a"])}).status_code == 200


def test_multi_workspace_user_switches_explicitly(tenants):
    carol = _user("ten_carol", "user")
    add_member(tenants["a"], carol, "member")
    add_member(tenants["b"], carol, "member")
    c = _client("ten_carol")
    assert [m["workspace_id"] for m in c.get("/api/workspaces/mine").json()["workspaces"]] \
        == sorted([tenants["a"], tenants["b"]])
    assert c.get("/api/workspaces/mine").json()["active"] == tenants["a"]   # lowest id default
    assert c.get("/api/workspaces/mine",
                 headers={"X-Workspace-Id": str(tenants["b"])}).json()["active"] == tenants["b"]


def test_user_without_workspace_gets_403_not_data(tenants):
    _user("ten_loner", "user")
    c = _client("ten_loner")
    assert c.get("/api/leads").status_code == 403
    assert c.get("/api/stats").status_code == 403


def test_no_workspace_raises_for_background_code():
    from app.db import _ws_var
    token = _ws_var.set(None)
    try:
        with pytest.raises(NoWorkspace):
            suppress("x@y.z")
    finally:
        _ws_var.reset(token)


# ------------------------------------------------------------ admin / users --

def test_workspace_admin_sees_only_own_members(tenants):
    ca, cb = tenants["ca"], tenants["cb"]
    ids_a = {u["id"] for u in ca.get("/api/users").json()["rows"]}
    assert tenants["alice"] in ids_a and tenants["bob"] not in ids_a
    created = ca.post("/api/users", json={"username": "ten_newhire", "password": "pw12345!"})
    assert created.status_code == 200
    new_id = created.json()["id"]
    assert [m["workspace_id"] for m in memberships(new_id)] == [tenants["a"]]
    assert new_id not in {u["id"] for u in cb.get("/api/users").json()["rows"]}
    assert cb.patch(f"/api/users/{new_id}", json={"username": "ten_newhire",
                    "role": "user", "full_name": "pwned"}).status_code == 404
    assert cb.delete(f"/api/users/{new_id}").status_code == 404
    assert query_one("SELECT 1 AS x FROM users WHERE id = ?", (new_id,))
    # deleting from A removes membership and (no other workspace) the account
    assert ca.delete(f"/api/users/{new_id}").status_code == 200
    assert query_one("SELECT 1 AS x FROM users WHERE id = ?", (new_id,)) is None


def test_subscriptions_cannot_be_managed_across_tenants(tenants):
    plan = query_one("SELECT id FROM plans LIMIT 1")
    if not plan:
        plan = {"id": execute(
            "INSERT INTO plans(name, price, period_days, search_limit) VALUES('p-ten',0,30,5)")}
    r = tenants["ca"].post(f"/api/users/{tenants['bob']}/subscriptions",
                           json={"plan_id": plan["id"]})
    assert r.status_code == 404
    assert tenants["cb"].get("/api/subscriptions").json() == [] or all(
        s["user_id"] != tenants["alice"] for s in tenants["cb"].get("/api/subscriptions").json())


def test_install_wide_controls_are_superadmin_only(tenants):
    ca = tenants["ca"]   # alice has role=admin, not superadmin
    assert ca.post("/api/automation/kill").status_code == 403
    assert ca.post("/api/automation/switch",
                   json={"name": "automation_master", "enabled": True}).status_code == 403
    assert ca.get("/api/automation/backups").status_code == 403
    assert ca.post("/api/automation/run/backup").status_code == 403
    assert ca.post("/api/plans", json={"name": "x"}).status_code == 403


def test_failures_and_log_are_scoped(tenants):
    with workspace_context(tenants["a"]):
        from app.db import log_failure, log_automation
        log_failure("t.job", "boom-A")
        log_automation("t", "msg-A")
    assert any(f["error"] == "boom-A" for f in tenants["ca"].get("/api/automation/failures").json())
    assert not any(f["error"] == "boom-A" for f in tenants["cb"].get("/api/automation/failures").json())
    assert not any(l["message"] == "msg-A" for l in tenants["cb"].get("/api/automation/log").json())


def test_workspace_admin_endpoints(tenants):
    admin = _client("admin")           # seeded superadmin
    created = admin.post("/api/workspaces", json={"name": "Tenant C"})
    assert created.status_code == 200
    assert admin.post("/api/workspaces", json={"name": "x"}).status_code == 200
    assert tenants["ca"].post("/api/workspaces", json={"name": "nope"}).status_code == 403
    assert tenants["ca"].get("/api/workspaces").status_code == 403
    # members
    members = tenants["ca"].get("/api/workspaces/current/members").json()
    assert [m["username"] for m in members] == ["ten_alice", "ten_carol"] or \
        "ten_alice" in [m["username"] for m in members]
    assert tenants["cb"].delete(
        f"/api/workspaces/current/members/{tenants['alice']}").status_code == 404
    # last owner can't be removed
    assert tenants["ca"].delete(
        f"/api/workspaces/current/members/{tenants['alice']}").status_code == 400


# ----------------------------------------------------- migration (upgrade) --

def test_migration_adopts_legacy_data_into_default_workspace():
    """Upgrade path for a real production DB: pre-tenancy tables with rows,
    migrated in place, nothing lost, nothing left unscoped."""
    from app import db

    root = pymysql.connect(host=config.settings.mysql_host, port=config.settings.mysql_port,
                           user=config.settings.mysql_user,
                           password=config.settings.mysql_password, autocommit=True)
    scratch = "leadgen_mig_test"
    with root.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {scratch}")
        cur.execute(f"CREATE DATABASE {scratch} CHARACTER SET utf8mb4")
    conn = Conn(host=config.settings.mysql_host, port=config.settings.mysql_port,
                user=config.settings.mysql_user, password=config.settings.mysql_password,
                database=scratch, autocommit=True, cursorclass=db.TranslateCursor)
    try:
        with conn.cursor() as cur:
            for stmt in db.CREATE_TABLES:          # the legacy (pre-tenancy) shape
                cur.execute(stmt)
            cur.execute("INSERT INTO users(username, password_hash, role) "
                        "VALUES('legacy_admin','x','superadmin'),('legacy_u','x','user')")
            cur.execute("INSERT INTO leads(business_name, place_id, city, phone, source) "
                        "VALUES('Old Lead','p1','Delhi','9999999999','Google'),"
                        "('Old Lead 2','p2','Delhi',NULL,NULL)")
            cur.execute("INSERT INTO interactions(lead_id, type, date) "
                        "SELECT id, 'call', 'x' FROM leads")
            cur.execute("INSERT INTO approval_queue(lead_id, status) SELECT id, 'PENDING' "
                        "FROM leads LIMIT 1")
            cur.execute("INSERT INTO templates(name, type, body) VALUES('t','email','b')")
            cur.execute("INSERT INTO suppression_list(value, kind) VALUES('a@b.c','email')")
            cur.execute("INSERT INTO failed_jobs(job, error) VALUES('j','e')")

        applied = run_migrations(conn)
        assert applied == [v for v, _, _ in MIGRATIONS]
        assert run_migrations(conn) == []                      # idempotent

        with conn.cursor() as cur:
            cur.execute("SELECT id FROM workspaces")
            (ws,) = [r["id"] for r in cur.fetchall()]
            for table in ("leads", "interactions", "approval_queue", "templates",
                          "suppression_list"):
                cur.execute(f"SELECT COUNT(*) AS n, SUM(workspace_id = ?) AS mine FROM {table}",
                            (ws,))
                row = cur.fetchone()
                assert row["n"] > 0 and row["n"] == row["mine"], table
            cur.execute("SELECT COUNT(*) AS n FROM leads")
            assert cur.fetchone()["n"] == 2                    # nothing lost
            cur.execute("SELECT COUNT(*) AS n FROM workspace_members WHERE workspace_id = ?",
                        (ws,))
            assert cur.fetchone()["n"] == 2                    # every user adopted
            cur.execute("SELECT m.role AS role FROM workspace_members m JOIN users u ON u.id = m.user_id "
                        "WHERE u.username = 'legacy_admin'")
            assert cur.fetchone()["role"] == "owner"
            cur.execute("SELECT workspace_id FROM failed_jobs")
            assert cur.fetchone()["workspace_id"] is None      # system logs stay unscoped
            # existing values get honest 'legacy' lineage, not silence or invention
            cur.execute("SELECT source, source_type, confidence, verified_at FROM field_sources "
                        "WHERE field = 'phone'")
            row = cur.fetchone()
            assert (row["source"], row["source_type"], row["confidence"]) == ("Google", "legacy", 0.5)
            assert row["verified_at"] is None
            cur.execute("SELECT COUNT(*) AS n FROM field_sources")
            assert cur.fetchone()["n"] == 1                    # only populated fields
            # new uniqueness is per workspace; old global key is gone
            cur.execute("SELECT COUNT(*) AS n FROM information_schema.statistics "
                        "WHERE table_schema = DATABASE() AND index_name = 'uq_leads_place_id'")
            assert cur.fetchone()["n"] == 0
    finally:
        conn.close()
        with root.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {scratch}")
        root.close()
