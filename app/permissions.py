"""Fine-grained feature permissions.

Roles give the broad shape (viewer = read-only, user = own data, admin = everything in the
workspace). On top of that an admin can switch individual *features* off for an individual user,
e.g. a junior who may work leads but must not send outreach or issue proposals.

A denied feature blocks every request into its API area - reads included - so the data can't be
read around the restriction. Admins and superadmins are never restricted (they could just undo
it), and a restriction can't be placed on them. Enforced in the request middleware, so no router
can forget to check it.
"""

from __future__ import annotations

import json

# feature -> (label shown to admins, API path prefixes it covers)
FEATURES: dict[str, tuple[str, tuple[str, ...]]] = {
    "search":    ("Lead search & targets", ("/api/search", "/api/targets", "/api/geo", "/api/discovery")),
    "outreach":  ("Outreach: templates, approvals & sending", ("/api/approvals", "/api/templates", "/api/suppression")),
    "campaigns": ("Campaigns & segments", ("/api/campaigns", "/api/segments")),
    "meetings":  ("Meetings", ("/api/meetings",)),
    "proposals": ("Proposals & revenue", ("/api/proposals",)),
    "tasks":     ("Tasks", ("/api/tasks",)),
    "tokens":    ("API tokens", ("/api/tokens",)),
}


def parse(raw) -> list[str]:
    """Stored value -> clean list of known feature names."""
    if isinstance(raw, list):
        items = raw
    else:
        try:
            items = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            items = []
    return [f for f in dict.fromkeys(items) if f in FEATURES]


def blocked_feature(user: dict | None, path: str) -> str | None:
    """The feature this request touches that `user` is denied, else None."""
    if not user or user.get("role") in ("admin", "superadmin"):
        return None
    denied = user.get("denied_features") or []
    for feature in denied:
        if feature in FEATURES and any(path == pre or path.startswith(pre + "/") for pre in FEATURES[feature][1]):
            return feature
    return None


def catalogue() -> list[dict]:
    return [{"key": k, "label": v[0]} for k, v in FEATURES.items()]
