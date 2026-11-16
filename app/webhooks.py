"""Outbound webhooks: POST a signed JSON event to your own URL (Zapier, n8n, a CRM).

Events: lead.hot, reply.received, message.sent, meeting.scheduled, proposal.sent/accepted/declined, ping. Each delivery carries
`X-Signature: sha256=<hex HMAC of the body with the webhook's secret>`. URLs are checked
against the SSRF guard (public addresses only) at save time and again at delivery.
Delivery runs on a background thread, never blocks the request, and is logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading

import httpx

from . import jobqueue, netsafe
from .db import current_ws_or_none, execute, query, query_one, utcnow, workspace_context

EVENTS = ("lead.hot", "reply.received", "message.sent", "meeting.scheduled", "proposal.sent",
          "proposal.accepted", "proposal.declined", "ping")


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def new_secret() -> str:
    return secrets.token_hex(24)


def _deliver(workspace_id: int, hook: dict, event: str, body: bytes) -> dict:
    """One HTTP attempt. Always logs a delivery row; returns {status_code, error}."""
    status, error = None, None
    try:
        netsafe.validate_url(hook["url"])
        r = httpx.post(hook["url"], content=body, timeout=8, follow_redirects=False,
                       headers={"Content-Type": "application/json", "X-Event": event,
                                "X-Signature": sign(hook["secret"], body)})
        status = r.status_code
        if status >= 400:
            error = f"HTTP {status}"
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:300]
    with workspace_context(workspace_id):
        execute("INSERT INTO webhook_deliveries(workspace_id, webhook_id, event, status_code, error, at) "
                "VALUES(?,?,?,?,?,?)", (workspace_id, hook["id"], event, status, error, utcnow()))
    return {"status_code": status, "error": error}


@jobqueue.handler("webhook")
def _webhook_job(payload: dict) -> None:
    """Queue handler: deliver, and raise on failure so the queue retries with backoff. A hook that
    was deleted or switched off since the event was queued is simply dropped."""
    hook = query_one("SELECT * FROM webhooks WHERE id = ? AND workspace_id = ? AND is_active = 1",
                     (payload["hook_id"], payload["workspace_id"]))
    if not hook:
        return
    result = _deliver(payload["workspace_id"], hook, payload["event"], payload["body"].encode())
    if result["error"]:
        raise RuntimeError(result["error"])


def emit(event: str, payload: dict, *, wait: bool = False) -> int:
    """Queue `event` for every active webhook of the current workspace that subscribes to it, and make
    the first attempt right away on a background thread. Failures are retried by the job queue
    (1m, 5m, 30m, 2h). Returns how many webhooks were targeted. Never raises."""
    try:
        wid = current_ws_or_none()
        if wid is None:
            return 0
        hooks = [h for h in query("SELECT * FROM webhooks WHERE workspace_id = ? AND is_active = 1", (wid,))
                 if event in (h["events"] or "").split(",")]
        body = json.dumps({"event": event, "at": utcnow(), "data": payload}, default=str)
        job_ids = [jobqueue.enqueue("webhook", {"hook_id": h["id"], "workspace_id": wid, "event": event, "body": body})
                   for h in hooks]
        threads = [threading.Thread(target=jobqueue.run_one, args=(j,), daemon=True) for j in job_ids]
        for t in threads:
            t.start()
        if wait:
            for t in threads:
                t.join(15)
        return len(hooks)
    except Exception:  # noqa: BLE001
        return 0
