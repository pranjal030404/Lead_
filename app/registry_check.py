"""Registry freshness: re-check registry-sourced leads (UK ch:<number>, India
mca:<CIN>), update changed data, retire dead companies, remove dead leftovers.

- check:  look the company up again (Companies House API if keyed, else the bulk
          file; data.gov.in API if keyed, plus any local MCA CSVs).
- update: refresh the registered address when it changed.
- retire: dissolved / liquidated / struck-off -> business_status CLOSED_PERMANENTLY,
          which already stops outreach and drops the score.
- remove: retired leads nobody ever worked (status NEW, no contact, no touchpoints)
          are deleted. Anything with history is kept, flagged closed - never deleted.

Conservative on purpose: a company that is simply *absent* from a source is
"unknown" and left alone; only an explicit non-active status retires a lead.
"""

from __future__ import annotations

import csv
import re
import zipfile

from . import http
from .config import settings
from .db import current_ws, execute, log_automation, log_failure, query, utcnow, workspace_context

UK_RETIRED = ("dissolved", "liquidation", "receivership", "administration", "converted",
              "closed", "insolvency", "strike")


def uk_retired(status: str | None) -> bool:
    s = (status or "").lower()
    return any(w in s for w in UK_RETIRED)


def india_retired(status: str | None) -> bool:
    s = (status or "").lower()
    if not s:
        return False
    if any(w in s for w in ("strike", "dissol", "liquidat", "amalgam", "inactive", "closed", "under process")):
        return True
    return "active" not in s


# ------------------------------------------------------------- lookups --

def uk_lookup(numbers: set[str]) -> dict[str, dict]:
    """number -> {status, address}. API per company if keyed, else one bulk-file pass."""
    out: dict[str, dict] = {}
    if settings.companies_house_api_key:
        for n in numbers:
            try:
                r = http.get(f"https://api.company-information.service.gov.uk/company/{n}",
                             breaker="companies_house", auth=(settings.companies_house_api_key, ""))
            except Exception as exc:  # noqa: BLE001
                log_failure("registry.uk", str(exc), n)
                continue
            if r.status_code == 200:
                j = r.json()
                a = j.get("registered_office_address") or {}
                out[n] = {"status": j.get("company_status"),
                          "address": ", ".join(str(a[k]) for k in ("address_line_1", "locality", "postal_code")
                                               if a.get(k)) or None}
        return out
    from .providers.uk_bulk import ensure_bulk_file, iter_rows
    with zipfile.ZipFile(ensure_bulk_file()) as z, z.open(z.namelist()[0]) as f:
        for row in iter_rows(f):
            n = (row.get("CompanyNumber") or "").strip()
            if n in numbers:
                out[n] = {"status": (row.get("CompanyStatus") or "").strip(),
                          "address": ", ".join(x for x in ((row.get("RegAddress.AddressLine1") or "").strip(),
                                                           (row.get("RegAddress.PostTown") or "").strip(),
                                                           (row.get("RegAddress.PostCode") or "").strip()) if x) or None}
    return out


def india_lookup(cins: set[str]) -> dict[str, dict]:
    """CIN -> {status, address}, from local CSVs and (if keyed) data.gov.in pages."""
    from .providers.india_mca import BASE, PAGE, _pick, row_to_candidate
    out: dict[str, dict] = {}

    def take(row: dict) -> None:
        cin = _pick(row, "cin", "corporate_identification_number", "corporate_identification_no")
        if cin in cins:
            c = row_to_candidate(row)
            out[cin] = {"status": _pick(row, "company_status", "status"),
                        "address": c["address"] if c else None}

    from .providers.india_csv import folder
    for path in sorted(folder().glob("*.csv")):
        with open(path, encoding="utf-8-sig", newline="", errors="replace") as f:
            for row in csv.DictReader(f):
                take(row)
    if settings.data_gov_in_api_key and settings.data_gov_in_mca_resources:
        for rid in [r.strip() for r in settings.data_gov_in_mca_resources.split(",") if r.strip()]:
            for page in range(10):
                try:
                    r = http.get(BASE + rid, breaker="data_gov_in", params={
                        "api-key": settings.data_gov_in_api_key, "format": "json",
                        "limit": PAGE, "offset": page * PAGE})
                except Exception as exc:  # noqa: BLE001
                    log_failure("registry.india", str(exc))
                    break
                if r.status_code >= 400:
                    break
                rows = r.json().get("records") or []
                for row in rows:
                    take(row)
                if len(rows) < PAGE:
                    break
    return out


# ---------------------------------------------------------------- job --

def _delete_lead(lead_id: int) -> None:
    ws = current_ws()
    execute("DELETE FROM interactions WHERE lead_id = ? AND workspace_id = ?", (lead_id, ws))
    execute("DELETE FROM approval_queue WHERE lead_id = ? AND workspace_id = ?", (lead_id, ws))
    execute("DELETE FROM field_sources WHERE entity_type = 'lead' AND entity_id = ? AND workspace_id = ?",
            (lead_id, ws))
    execute("DELETE FROM leads WHERE id = ? AND workspace_id = ?", (lead_id, ws))


def untouched(lead: dict) -> bool:
    return (lead.get("status") or "NEW") == "NEW" and not lead.get("total_touchpoints") \
        and not lead.get("last_contacted_date") and not lead.get("first_contacted_date")


def decide(lead: dict, info: dict | None, retired_fn) -> dict:
    """Pure: what to do for one lead given the registry's current view."""
    if not info:
        return {"action": "unknown"}
    retired = retired_fn(info.get("status"))
    closed = (lead.get("business_status") or "").upper() == "CLOSED_PERMANENTLY"
    if retired:
        return {"action": "remove" if untouched(lead) else "retire"}
    changes = {}
    if closed:
        changes["business_status"] = "OPERATIONAL"      # came back to active
    new_addr = info.get("address")
    if new_addr and new_addr != (lead.get("address") or ""):
        changes["address"] = new_addr
        m = re.search(r"\b(\d{6})\b", new_addr)
        if m:
            changes["pincode"] = m.group(1)
    return {"action": "update" if changes else "ok", "changes": changes}


def check_workspace(limit: int, dry_run: bool = False) -> dict:
    leads = query("""SELECT * FROM leads WHERE workspace_id = ? AND (place_id LIKE 'ch:%%' OR place_id LIKE 'mca:%%')
                     ORDER BY updated_at ASC LIMIT ?""", (current_ws(), limit))
    uk = {l["place_id"][3:]: l for l in leads if l["place_id"].startswith("ch:")}
    ind = {l["place_id"][4:]: l for l in leads if l["place_id"].startswith("mca:")}
    stats = {"checked": len(leads), "updated": 0, "retired": 0, "removed": 0, "unknown": 0}
    for lookup, group, retired_fn in ((uk_lookup, uk, uk_retired), (india_lookup, ind, india_retired)):
        if not group:
            continue
        try:
            found = lookup(set(group))
        except Exception as exc:  # noqa: BLE001 - one registry down must not stop the other
            log_failure("registry.check", str(exc))
            stats["unknown"] += len(group)
            continue
        for key, lead in group.items():
            d = decide(lead, found.get(key), retired_fn)
            act = d["action"]
            if act == "unknown":
                stats["unknown"] += 1
            elif dry_run:
                stats[{"remove": "removed", "retire": "retired", "update": "updated"}.get(act, "unknown")] += (
                    act in ("remove", "retire", "update"))
            elif act == "remove":
                _delete_lead(lead["id"])
                stats["removed"] += 1
            elif act == "retire":
                execute("UPDATE leads SET business_status = 'CLOSED_PERMANENTLY', updated_at = ? "
                        "WHERE id = ? AND workspace_id = ?", (utcnow(), lead["id"], current_ws()))
                stats["retired"] += 1
            elif act == "update":
                ch = d["changes"]
                sets = ", ".join(f"{k} = ?" for k in ch)
                execute(f"UPDATE leads SET {sets}, updated_at = ? WHERE id = ? AND workspace_id = ?",
                        (*ch.values(), utcnow(), lead["id"], current_ws()))
                stats["updated"] += 1
            else:   # ok - still bump updated_at so the next run checks other leads first
                execute("UPDATE leads SET updated_at = ? WHERE id = ? AND workspace_id = ?",
                        (utcnow(), lead["id"], current_ws()))
    return stats


def run(workspace_ids: list[int], limit: int = 300, dry_run: bool = False) -> dict:
    total = {"checked": 0, "updated": 0, "retired": 0, "removed": 0, "unknown": 0}
    for wid in workspace_ids:
        with workspace_context(wid):
            s = check_workspace(limit, dry_run)
            for k in total:
                total[k] += s[k]
            if s["checked"]:
                log_automation("registry", f"Registry check{' (dry run)' if dry_run else ''}: "
                               f"{s['checked']} checked, {s['updated']} updated, {s['retired']} retired, "
                               f"{s['removed']} removed, {s['unknown']} not found")
    return total


# ----------------------------------------------------- business owners --

def uk_officers(number: str) -> list[dict]:
    """Current directors of a UK company (Companies House API - needs the free key)."""
    if not settings.companies_house_api_key:
        raise RuntimeError("Owner names for UK companies need COMPANIES_HOUSE_API_KEY (free)")
    r = http.get(f"https://api.company-information.service.gov.uk/company/{number}/officers",
                 breaker="companies_house", auth=(settings.companies_house_api_key, ""))
    if r.status_code != 200:
        raise RuntimeError(f"Companies House error {r.status_code}")
    out = []
    for o in r.json().get("items", []):
        if o.get("resigned_on"):
            continue
        name = o.get("name") or ""
        if "," in name:                       # "SMITH, John" -> "John Smith"
            last, first = [x.strip() for x in name.split(",", 1)]
            name = f"{first} {last}".title()
        out.append({"name": name, "role": o.get("officer_role"), "appointed_on": o.get("appointed_on")})
    return out
