from datetime import date, timedelta

from app.providers.india_mca import IndiaMcaProvider, parse_date, row_to_candidate
from app import automation


def _row(name, days_ago, status="Active", state="Karnataka"):
    d = (date.today() - timedelta(days=days_ago)).strftime("%d-%m-%Y")
    return {"CORPORATE_IDENTIFICATION_NUMBER": "U72900KA2026PTC000001", "COMPANY_NAME": name,
            "COMPANY_STATUS": status, "DATE_OF_REGISTRATION": d, "REGISTERED_STATE": state,
            "REGISTERED_OFFICE_ADDRESS": "12 MG Road Bengaluru 560001",
            "PRINCIPAL_BUSINESS_ACTIVITY_AS_PER_CIN": "Computer programming"}


def test_parse_date_formats():
    assert parse_date("05-03-2026") == date(2026, 3, 5)
    assert parse_date("2026-03-05") == date(2026, 3, 5)
    assert parse_date("garbage") is None


def test_row_to_candidate():
    c = row_to_candidate(_row("ACME LABS PRIVATE LIMITED", 10))
    assert c["business_name"] == "Acme Labs Private Limited"
    assert c["pincode"] == "560001" and c["country"] == "India"
    assert c["place_id"].startswith("mca:U72900")


def test_search_filters(monkeypatch):
    from app.providers import india_mca as m
    monkeypatch.setattr(m.settings, "data_gov_in_api_key", "k", raising=False)
    monkeypatch.setattr(m.settings, "data_gov_in_mca_resources", "abc", raising=False)
    rows = [_row("Acme Software Pvt Ltd", 20), _row("Old Software Pvt Ltd", 900),
            _row("Dead Software Pvt Ltd", 5, status="Struck Off"),
            _row("Bakery Pvt Ltd", 5)]

    class R:
        status_code = 200
        def json(self): return {"records": rows}
    monkeypatch.setattr(m.http, "get", lambda *a, **k: R())
    out = IndiaMcaProvider().search("software", "")
    assert [c["business_name"] for c in out] == ["Acme Software Pvt Ltd"]


def test_startup_runs_use_combined():
    assert {r[0] for r in automation._startup_runs()} == {"uk_all", "india_all"}


def test_uk_bulk_filter(monkeypatch, tmp_path):
    import io, zipfile
    from app.providers import uk_bulk
    d = (date.today() - timedelta(days=10)).strftime("%d/%m/%Y")
    csvtxt = ("CompanyName,CompanyNumber,CompanyStatus,IncorporationDate,RegAddress.AddressLine1,"
              "RegAddress.PostTown,RegAddress.PostCode,SICCode.SicText_1\n"
              f"ACME SOFTWARE LTD,123,Active,{d},1 High St,LONDON,E1 1AA,62012 - software\n"
              f"OLD SOFTWARE LTD,124,Active,01/01/2015,1 High St,LONDON,E1 1AA,62012\n"
              f"BAKERY LTD,125,Active,{d},2 High St,LONDON,E1 1AA,10710 - bakery\n")
    zp = tmp_path / "b.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("x.csv", csvtxt)
    monkeypatch.setattr(uk_bulk, "ensure_bulk_file", lambda: zp)
    out = uk_bulk.UkBulkProvider().search("software", "London")
    assert [c["business_name"] for c in out] == ["Acme Software Ltd"]


def test_india_csv(monkeypatch, tmp_path):
    from app.providers import india_csv
    monkeypatch.setattr(india_csv, "folder", lambda: tmp_path)
    r = _row("Acme Software Pvt Ltd", 20)
    (tmp_path / "ka.csv").write_text(",".join(r) + "\n" + ",".join(f'"{v}"' for v in r.values()) + "\n")
    out = india_csv.IndiaCsvProvider().search("software", "")
    assert [c["business_name"] for c in out] == ["Acme Software Pvt Ltd"]


def test_combined_merges_and_survives_failure():
    from app.providers.combined import CombinedProvider

    class A:
        name = "a"
        def search(self, *a, **k): return [{"place_id": "ch:1", "business_name": "X"},
                                           {"place_id": "ch:2", "business_name": "Y"}]
    class B:
        name = "b"
        def search(self, *a, **k): return [{"place_id": "ch:2", "business_name": "Y"}]
    class Bad:
        name = "bad"
        def search(self, *a, **k): raise RuntimeError("no key")
    assert len(CombinedProvider("c", [A(), B(), Bad()]).search("x", "")) == 2
    import pytest
    with pytest.raises(RuntimeError):
        CombinedProvider("c", [Bad()]).search("x", "")


def test_registry_decisions():
    from app.registry_check import decide, uk_retired, india_retired
    fresh = {"status": "NEW", "total_touchpoints": 0, "address": "old", "business_status": "OPERATIONAL"}
    worked = {**fresh, "status": "CONTACTED", "total_touchpoints": 2}
    dead = {"status": "dissolved"}
    assert decide(fresh, dead, uk_retired)["action"] == "remove"
    assert decide(worked, dead, uk_retired)["action"] == "retire"       # history kept
    assert decide(fresh, None, uk_retired)["action"] == "unknown"       # absent = leave alone
    up = decide(fresh, {"status": "active", "address": "new 560001"}, uk_retired)
    assert up["action"] == "update" and up["changes"]["address"] == "new 560001"
    back = decide({**fresh, "business_status": "CLOSED_PERMANENTLY", "address": "x"},
                  {"status": "active", "address": "x"}, uk_retired)
    assert back["changes"] == {"business_status": "OPERATIONAL"}
    assert india_retired("Strike Off") and india_retired("Amalgamated") and not india_retired("Active")
