"""Core behaviour tests. Run with: python -m pytest tests -q

These use the mock provider, so they need no API key and make no network calls.
Environment (PROVIDER, MYSQL_*) and the isolated MySQL test database are set up
in tests/conftest.py, which runs before this module is imported.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402

from app.db import init_db, query, query_one, scalar, utcnow  # noqa: E402
from app.dedup import find_duplicate, normalise_name, similarity  # noqa: E402
from app.outreach import is_suppressed, render_template, suppress  # noqa: E402
from app.scoring import score_lead  # noqa: E402
from app.search_service import (find_cached_search, get_coverage, run_search,
                                search_fingerprint)  # noqa: E402
from app.validation import data_quality_score, normalise_phone, validate_email  # noqa: E402

init_db()


# ------------------------------------------------------------ validation --

def test_indian_phone_normalisation():
    assert normalise_phone("+91 98765-43210", "India") == ("9876543210", True)
    assert normalise_phone("098765 43210", "India") == ("9876543210", True)
    assert normalise_phone("12345", "India")[1] is False


def test_international_phone_kept_in_full():
    cleaned, valid = normalise_phone("+1 (512) 555-0142", "USA")
    assert cleaned == "+15125550142" and valid


def test_indian_landline_is_valid_but_not_whatsappable():
    """Faridabad landline: STD 129 + subscriber. Many real businesses list only
    these, so they must validate - but a wa.me link to one is a dead chat."""
    from app.validation import whatsapp_number

    cleaned, valid = normalise_phone("+91 129 297 4246", "India")
    assert (cleaned, valid) == ("1292974246", True)
    assert whatsapp_number(cleaned, "India") is None
    assert whatsapp_number("9876543210", "India") == "919876543210"


def test_email_validation_flags_disposable_and_role_addresses():
    assert validate_email("owner@shop.com")[1] is True
    assert validate_email("test@mailinator.com")[1] is False
    assert validate_email("noreply@shop.com")[1] is False
    assert validate_email("not-an-email")[1] is False


def test_data_quality_score_caps_at_ten():
    lead = {"phone": "9876543210", "phone_valid": 1, "email": "a@b.com", "address": "x",
            "owner_name": "Asha", "instagram_url": "http://ig", "has_website": 1}
    assert data_quality_score(lead) == 10


# --------------------------------------------------------------- scoring --

def test_no_website_scores_higher_than_good_website():
    no_site = score_lead({"phone": "9876543210", "phone_valid": 1, "address": "x",
                          "owner_name": "A", "has_website": 0, "google_reviews": 40})
    good_site = score_lead({"phone": "9876543210", "phone_valid": 1, "address": "x",
                            "owner_name": "A", "has_website": 1, "website_score": 9,
                            "website_url": "https://x.com", "google_reviews": 40})
    assert no_site["lead_score"] > good_site["lead_score"]
    assert no_site["lead_priority"] == "HOT"


def test_permanently_closed_is_excluded():
    result = score_lead({"business_status": "CLOSED_PERMANENTLY", "has_website": 0})
    assert result["lead_score"] == 0 and result["lead_priority"] == "EXCLUDED"


def test_thin_data_caps_the_score():
    result = score_lead({"has_website": 0})  # no phone, no address, no owner
    assert result["data_quality_score"] < 3
    assert result["lead_score"] <= 5


def test_contactable_business_with_no_website_reaches_hot_without_review_data():
    """OpenStreetMap carries no review counts. HOT must still be reachable, or
    the operator's main working queue is permanently empty on the free provider."""
    result = score_lead({"has_website": 0, "website_url": None, "phone": "9876543210",
                         "phone_valid": 1, "address": "12 Main Rd", "owner_name": "A",
                         "google_reviews": None})
    assert result["lead_priority"] == "HOT"


def test_uncontactable_lead_stays_cold():
    result = score_lead({"has_website": 0, "website_url": None, "google_reviews": None})
    assert result["lead_priority"] == "COLD"


def test_dead_website_is_not_reported_as_having_no_website():
    """A listed-but-broken site is a different (better) pitch than no site at
    all, and the outreach copy must not claim they have no website."""
    from app.scoring import observation_for

    lead = {"website_url": "https://example.invalid", "has_website": 0,
            "website_issues": '["Doesn\'t load at all - the domain isn\'t responding"]',
            "phone": "9876543210", "phone_valid": 1, "address": "x"}
    result = score_lead(lead)
    reasons = result["score_reasons"]
    assert "no website at all" not in reasons
    assert "doesn't load" in reasons

    observation = observation_for(lead)
    assert "don't have a website" not in observation
    assert observation.startswith("your site")


def test_score_always_explains_itself():
    result = score_lead({"has_website": 0, "phone": "9876543210", "phone_valid": 1,
                         "address": "x", "owner_name": "A"})
    assert result["score_reasons"] and "no website" in result["score_reasons"]


# ----------------------------------------------------------------- dedup --

def test_name_normalisation_strips_noise_words():
    assert normalise_name("The Sharma Dental Clinic Pvt Ltd") == "sharma dental clinic"


def test_fuzzy_similarity_catches_variants():
    assert similarity("Sharma's Dental", "Sharma Dental Clinic") >= 0.80
    assert similarity("Sharma Dental", "Gupta Motors") < 0.5


# ------------------------------------------- search, coverage, idempotency --

def test_search_creates_leads_and_coverage():
    result = run_search(niche="restaurants", city="Testville", max_results=10,
                        provider_name="mock", enrich=False)
    assert result["status"] == "COMPLETED"
    assert result["new_leads"] > 0

    coverage = get_coverage("restaurants", "Testville")
    assert coverage["status"] == "FRESH"
    assert coverage["times_searched"] == 1


def test_rerunning_the_same_search_creates_no_duplicates():
    """Part 7.7 - search the same tiny area twice, confirm zero new leads.
    Uses an automated (non-manual) trigger so it always scrapes fresh - a second
    manual run of the same query would be served from the search cache instead
    of exercising the dedup path (see the cache tests below)."""
    before = scalar("SELECT COUNT(*) FROM leads WHERE city = 'Testville'")
    result = run_search(niche="restaurants", city="Testville", max_results=10,
                        provider_name="mock", enrich=False, triggered_by="target")
    after = scalar("SELECT COUNT(*) FROM leads WHERE city = 'Testville'")

    assert result["new_leads"] == 0
    assert result["duplicates_skipped"] > 0
    assert before == after


def test_coverage_goes_exhausted_after_three_barren_runs():
    for _ in range(3):
        run_search(niche="restaurants", city="Testville", max_results=10,
                   provider_name="mock", enrich=False, triggered_by="target")
    assert get_coverage("restaurants", "Testville")["status"] == "EXHAUSTED"


def test_permanently_closed_businesses_are_not_stored():
    run_search(niche="cafes", city="Closedtown", max_results=20,
               provider_name="mock", enrich=False)
    closed = query(
        "SELECT business_status FROM leads WHERE city = 'Closedtown' "
        "AND business_status = 'CLOSED_PERMANENTLY'"
    )
    assert closed == []


# --------------------------------------------------------- shared search cache --

def test_identical_manual_search_is_served_from_cache_with_zero_api_calls():
    first = run_search(niche="restaurants", city="Cachelia", max_results=10,
                       provider_name="mock", enrich=False)
    assert first["status"] == "COMPLETED" and first["new_leads"] > 0

    second = run_search(niche="restaurants", city="Cachelia", max_results=10,
                        provider_name="mock", enrich=False)
    assert second["from_cache"] is True
    assert second["api_calls"] == 0
    assert second["cached_from"] == first["search_id"]

    cached = find_cached_search("restaurants", "Cachelia", provider_name="mock")
    assert cached and cached["search_id"] == first["search_id"]

    cached_row = query_one("SELECT cached_from FROM searches WHERE id = ?", (second["search_id"],))
    assert cached_row["cached_from"] == first["search_id"]
    original_leads = scalar("SELECT COUNT(*) FROM leads WHERE search_id = ?",
                            (first["search_id"],))
    assert original_leads > 0


def test_changed_query_is_a_cache_miss():
    run_search(niche="restaurants", city="Cachelia", max_results=10,
               provider_name="mock", enrich=False)
    changed = run_search(niche="restaurants", city="Cachelia", area="Model Town",
                         max_results=10, provider_name="mock", enrich=False)
    assert changed.get("from_cache", False) is False


def test_automated_searches_bypass_the_cache():
    run_search(niche="restaurants", city="Cachelia", max_results=10,
               provider_name="mock", enrich=False)
    sweep = run_search(niche="restaurants", city="Cachelia", max_results=10,
                       provider_name="mock", enrich=False, triggered_by="automation")
    assert sweep.get("from_cache", False) is False
    assert sweep["duplicates_skipped"] > 0, "a fresh scrape must re-check the DB"


def test_fingerprint_is_case_and_space_insensitive():
    base = search_fingerprint("Restaurants ", "Delhi", " Sector 15 ", "INDIA", "google")
    assert base == search_fingerprint("restaurants", "delhi", "Sector 15", "india", "google")
    assert base != search_fingerprint("restaurants", "Mumbai", "Sector 15", "india", "google")
    assert base != search_fingerprint("cafes", "Delhi", "Sector 15", "india", "google")


def test_cache_expires_after_ttl_then_scrapes_fresh():
    from app.db import execute

    first = run_search(niche="hotels", city="Ancientville", max_results=10,
                       provider_name="mock", enrich=False)
    assert first["status"] == "COMPLETED"

    execute(
        "UPDATE search_cache SET created_at = '2000-01-01T00:00:00+00:00' "
        "WHERE fingerprint = ?",
        (search_fingerprint("hotels", "Ancientville", provider_name="mock"),),
    )
    assert find_cached_search("hotels", "Ancientville", provider_name="mock") is None, \
        "an expired snapshot must not be served"

    refreshed = run_search(niche="hotels", city="Ancientville", max_results=10,
                           provider_name="mock", enrich=False)
    assert refreshed.get("from_cache", False) is False, "expired cache must scrape fresh"
    assert refreshed["search_id"] != first["search_id"]

    recovered = find_cached_search("hotels", "Ancientville", provider_name="mock")
    assert recovered and recovered["search_id"] == refreshed["search_id"], \
        "the fresh scrape must reset the TTL so the cache serves again"


# ------------------------------------------------------------- suppression --

def test_suppression_blocks_a_recipient():
    suppress("stop@example.com", "email", "test opt-out")
    assert is_suppressed("STOP@example.com") is True
    assert is_suppressed("someone-else@example.com") is False


# --------------------------------------------------------------- templates --

def test_template_merge_fills_every_field():
    lead = query_one("SELECT * FROM leads WHERE city = 'Testville' LIMIT 1")
    rendered = render_template("cold_email_first", lead)
    assert lead["business_name"] in rendered["subject"]
    assert "{" not in rendered["body"], "a merge field was left unfilled"
    assert rendered["unfilled"] == []


def test_possessive_handles_names_ending_in_s():
    from app.outreach import possessive

    assert possessive("Sharma Dental") == "Sharma Dental's"
    assert possessive("Django Meals") == "Django Meals'"


def test_observation_is_specific_for_a_lead_with_no_website():
    from app.scoring import observation_for

    assert "website" in observation_for({"website_url": None}).lower()


# -------------------------------------------------------------- quota guard --

def test_quota_hard_stops_rather_than_warning():
    from app.quota import QuotaExceeded, check_and_reserve

    config.settings.max_google_calls_per_day = 2
    check_and_reserve("quota_test_service")
    check_and_reserve("quota_test_service")
    try:
        check_and_reserve("quota_test_service")
        raise AssertionError("expected QuotaExceeded on the third call")
    except QuotaExceeded as exc:
        assert exc.cap == 2
    finally:
        config.settings.max_google_calls_per_day = 500


# ---------------------------------------------------------------- alerting --

def test_alert_claim_is_idempotent():
    """The claim is what makes alerting safe to call from every code path that
    creates leads - the second attempt on a key must not send again."""
    from app.notify import _claim

    assert _claim("test:once", "unit") is True
    assert _claim("test:once", "unit") is False


def test_hot_lead_alert_fires_once_and_only_above_the_threshold():
    from app import notify
    from app.db import execute

    threshold = config.settings.hot_alert_min_score
    now = utcnow()
    hot = execute(
        "INSERT INTO leads(business_name, city, lead_score, lead_priority, created_at) "
        "VALUES(?,?,?,?,?)",
        ("Alertable Cafe", "Testville", threshold, "HOT", now),
    )
    cold = execute(
        "INSERT INTO leads(business_name, city, lead_score, lead_priority, created_at) "
        "VALUES(?,?,?,?,?)",
        ("Middling Gym", "Testville", threshold - 2, "WARM", now),
    )

    first = notify.alert_new_hot_leads([hot, cold])
    assert first["alerted"] == 1, "only the lead at or above the threshold alerts"

    again = notify.alert_new_hot_leads([hot, cold])
    assert again["alerted"] == 0, "an already-alerted lead must never re-alert"


def test_the_alert_threshold_is_actually_reachable():
    """The blueprint asks for an alert at 9-10, but the top achievable score is
    8: the only rule that could push past it is the social bonus, which needs
    socials scraped from a website an ideal prospect doesn't have. A threshold
    above the real ceiling is an alert that silently never fires, so this locks
    the two together - change scoring and this test tells you to re-check it."""
    ideal = score_lead({
        "business_name": "Ideal Prospect",
        "website_url": None, "has_website": 0,
        "phone": "9876543210", "phone_valid": 1,
        "address": "12 Main Road, Faridabad", "city": "Faridabad",
        "google_reviews": None,
    })
    assert ideal["lead_priority"] == "HOT"
    assert ideal["lead_score"] >= config.settings.hot_alert_min_score, (
        f"an ideal prospect scores {ideal['lead_score']} but the hot-lead alert "
        f"only fires at {config.settings.hot_alert_min_score} - it would never fire"
    )


def test_quota_alert_warns_once_per_day_on_the_approach():
    from app import notify
    from app.db import execute

    execute("DELETE FROM alerts_sent WHERE kind = 'quota'")
    assert notify.alert_quota(50, 100)["skipped"] == "under threshold"
    # No RESEND_API_KEY in tests, so a fired alert reports the missing mail
    # config - but it claimed the key and wrote to the activity feed first.
    assert notify.alert_quota(80, 100)["logged"] is True, "80% must warn"
    assert notify.alert_quota(95, 100)["skipped"] == "already alerted"


def test_operator_alerts_ignore_the_suppression_list():
    """Outreach must respect suppression; a health check must not. If the
    operator's own address landed on it, alerts would die silently."""
    from app import notify

    suppress("owner@example.com", "email", "test")
    config.settings.alert_email = "owner@example.com"
    try:
        assert notify.recipient() == "owner@example.com"
        # No RESEND_API_KEY in tests, so this stops before the network - the
        # point is that it stops there and not at a suppression check.
        assert notify.send_digest("health check")["skipped"] == "email not configured"
    finally:
        config.settings.alert_email = ""


# ----------------------------------------------------------------- backups --

def test_backup_sync_is_skipped_when_no_remote_is_configured():
    from app.backup import sync_offsite

    config.settings.backup_remote = ""
    dummy = Path(config.settings.backup_dir) / "dummy.sql"
    assert sync_offsite(dummy)["skipped"] == "BACKUP_REMOTE not set"


def test_backup_sync_failure_never_raises():
    """A dead remote must not cost you the local backup that already worked."""
    from app.backup import sync_offsite

    dummy = Path(config.settings.backup_dir) / "dummy.sql"
    config.settings.backup_remote = "nosuchremote:bucket"
    config.settings.rclone_path = "definitely-not-a-real-binary"
    try:
        result = sync_offsite(dummy)
        assert result["ok"] is False and "not found" in result["error"]
    finally:
        config.settings.backup_remote = ""
        config.settings.rclone_path = "rclone"


def test_backup_writes_a_restorable_file():
    """mysqldump output must be a real SQL dump carrying the app's data."""
    from app.backup import run_backup

    config.settings.backup_remote = ""
    result = run_backup()
    path = Path(result["file"])
    assert path.exists() and path.stat().st_size > 0

    content = path.read_text(encoding="utf-8", errors="replace")
    assert "CREATE TABLE" in content, "dump is not a SQL dump"
    assert "`leads`" in content, "dump misses the leads table"
    assert "INSERT INTO `leads`" in content, "dump carries no lead rows"


# --------------------------------------------------------------- job runner --

def test_long_running_job_starts_in_a_background_thread():
    """Regression: run_job_now used threading without importing it, so every
    long-running job button raised NameError instead of starting."""
    from app.automation import run_job_now

    assert run_job_now("scoring")["started"] is True


def test_alert_prune_only_removes_old_quota_claims():
    """Timestamps are written as '...T...+00:00'; SQLite's datetime('now') uses
    a space and no offset, so the cutoff must be built in Python to compare."""
    from app import notify
    from app.db import execute, scalar

    execute("REPLACE INTO alerts_sent(alert_key, kind, created_at) "
            "VALUES('quota:google:ancient','quota','2000-01-01T00:00:00+00:00')")
    execute("REPLACE INTO alerts_sent(alert_key, kind, created_at) "
            "VALUES('hot:99999','hot_lead','2000-01-01T00:00:00+00:00')")

    assert notify.prune(days=90) >= 1
    assert scalar("SELECT COUNT(*) FROM alerts_sent WHERE alert_key = 'quota:google:ancient'") == 0
    assert scalar("SELECT COUNT(*) FROM alerts_sent WHERE alert_key = 'hot:99999'") == 1, \
        "hot-lead claims must survive pruning or a lead could alert twice"


# ---------------------------------------------------------------- serving --

def test_static_assets_are_revalidated_not_blindly_cached():
    """There's no build step and no hashed filenames, so without a no-cache
    header the browser keeps running the app.js it cached before a deploy.
    Caught live: an edited app.js kept serving stale in the browser."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        for asset in ("/static/app.js", "/static/styles.css"):
            response = client.get(asset)
            assert response.status_code == 200, asset
            assert response.headers.get("cache-control") == "no-cache", asset


def test_health_endpoint_needs_no_auth():
    """An uptime monitor has no session cookie."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"


# ------------------------------------------------------------ scale/perf --

def test_thread_local_connections_are_reused():
    """Opening a connection per query cost ~9ms of the ~9ms a trivial SELECT
    took. Same thread must get the same handle back."""
    from app.db import close_conn, get_conn

    with get_conn() as first:
        pass
    with get_conn() as second:
        pass
    assert first is second
    close_conn()


def test_each_thread_gets_its_own_connection():
    """SQLite connections are not safe to share across threads - the pooling
    must not accidentally hand one thread's handle to another."""
    import threading

    from app.db import close_conn, get_conn

    seen = {}

    def grab(name):
        with get_conn() as conn:
            seen[name] = id(conn)
        close_conn()

    a = threading.Thread(target=grab, args=("a",))
    b = threading.Thread(target=grab, args=("b",))
    a.start(); b.start(); a.join(); b.join()
    assert seen["a"] != seen["b"]


def test_list_endpoint_returns_only_the_columns_the_table_draws():
    """56 columns x 50 rows is ~86KB per page for a table that reads 12 of
    them. full=true still gives everything."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers.leads import LIST_COLUMNS

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": config.settings.admin_user,
                                             "password": config.settings.admin_password})
        slim = client.get("/api/leads?limit=5").json()["leads"]
        wide = client.get("/api/leads?limit=5&full=true").json()["leads"]

    assert slim, "expected leads from the seeded searches"
    assert set(slim[0]) == set(LIST_COLUMNS)
    assert len(wide[0]) > len(slim[0]), "full=true must return the whole row"
    assert "score_reasons" in wide[0]


def test_fast_json_skips_the_encoder_but_matches_its_output():
    """The speed-up is only safe if it serialises the same values."""
    import json

    from app.fastjson import rows_response

    payload = {"total": 2, "leads": [
        {"id": 1, "name": "Cafe", "score": 8, "tags": ["a", "b"], "phone": None},
        {"id": 2, "name": "Cafe é", "score": 4.5, "tags": [], "phone": "99"},
    ]}
    body = json.loads(rows_response(payload).body)
    assert body == payload


def test_rate_limiter_prunes_stale_keys():
    """_hits kept one entry per client IP forever - a slow leak for anything
    exposed to more than one address."""
    import app.security as sec

    sec._hits.clear()
    sec._last_sweep = 0
    for i in range(50):
        sec.rate_limit(f"ip-{i}", limit=5, window_seconds=1)
    assert sec.rate_limit_state()["tracked_keys"] == 50

    # Age every entry past the window, then force a sweep.
    for key in sec._hits:
        sec._hits[key] = [0.0]
    sec._last_sweep = 0
    sec.rate_limit("trigger", limit=5, window_seconds=1)
    assert sec.rate_limit_state()["tracked_keys"] <= 2, "stale keys were not pruned"


def test_client_ip_only_trusts_forwarded_headers_when_configured():
    """X-Forwarded-For is attacker-controlled unless a proxy sets it, so
    trusting it unproxied would make the rate limiter trivially bypassable."""
    from app.security import client_ip

    class FakeRequest:
        def __init__(self, headers, host):
            self.headers = headers
            self.client = type("C", (), {"host": host})()

    spoofed = FakeRequest({"x-forwarded-for": "1.2.3.4, 10.0.0.1"}, "10.0.0.9")

    config.settings.trust_proxy = False
    assert client_ip(spoofed) == "10.0.0.9", "must ignore the header when untrusted"

    config.settings.trust_proxy = True
    try:
        assert client_ip(spoofed) == "1.2.3.4", "must take the original client"
    finally:
        config.settings.trust_proxy = False


# ---------------------------------------------------------------- geo --

def test_scheduler_lease_admits_exactly_one_holder():
    """uvicorn --workers 4 must not mean four schedulers running every job."""
    import app.automation as A
    from app.db import execute

    execute("DELETE FROM scheduler_lease")
    original = A.OWNER
    try:
        assert A.claim_lease() is True
        A.OWNER = "someone-else:1"
        assert A.claim_lease() is False, "a second worker must not also win"
        A.OWNER = original
        assert A.claim_lease() is True, "the holder must be able to renew"

        execute("UPDATE scheduler_lease SET expires_at = '2000-01-01T00:00:00'")
        A.OWNER = "someone-else:1"
        assert A.claim_lease() is True, "an expired lease must be takeable"
    finally:
        A.OWNER = original
        execute("DELETE FROM scheduler_lease")


def test_nearby_search_centres_on_the_coordinate():
    """'Search near me' hands the provider a point instead of a city name."""
    result = run_search(niche="cafes", city="Pinpoint", max_results=5,
                        provider_name="mock", enrich=False,
                        lat=28.6139, lng=77.2090, radius_m=2000)
    assert result["status"] == "COMPLETED" and result["new_leads"] > 0

    rows = query("SELECT lat, lng FROM leads WHERE city = 'Pinpoint'")
    assert rows, "the search stored no leads"
    for row in rows:
        assert abs(row["lat"] - 28.6139) < 0.1, "lead landed nowhere near the point"
        assert abs(row["lng"] - 77.2090) < 0.1
