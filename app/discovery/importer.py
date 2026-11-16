"""Persist parsed candidates through the normal dedupe -> insert -> enrich path."""

from __future__ import annotations

from ..db import get_conn, log_failure, safe_rollback
from ..dedup import find_duplicate, merge_into_existing
from ..enrich import enrich_many


def import_candidates(candidates: list[dict], default_niche: str, user_id: int | None,
                      enrich: bool = True) -> dict:
    from ..search_service import _insert_lead  # late import: avoids a cycle at load
    stats = {"received": len(candidates), "created": 0, "merged": 0, "failed": 0}
    new_ids: list[int] = []
    for cand in candidates:
        cand.setdefault("niche", default_niche)
        niche = cand.get("niche") or default_niche or "imported"
        with get_conn() as conn:
            conn.execute("START TRANSACTION")
            try:
                existing, _ = find_duplicate(conn, cand, owner_user_id=user_id)
                if existing:
                    merge_into_existing(conn, existing, cand)
                    stats["merged"] += 1
                else:
                    new_ids.append(_insert_lead(conn, cand, niche, None, owner_user_id=user_id))
                    stats["created"] += 1
                conn.execute("COMMIT")
            except Exception as exc:  # noqa: BLE001 - one bad row must not sink the batch
                safe_rollback(conn)
                stats["failed"] += 1
                log_failure("import", str(exc))
    if enrich and new_ids:
        try:
            stats["enrichment"] = enrich_many(new_ids)
        except Exception as exc:  # noqa: BLE001
            log_failure("import.enrich", str(exc))
    stats["lead_ids"] = new_ids[:100]
    return stats
