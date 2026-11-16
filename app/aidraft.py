"""Optional local-AI message drafting with Ollama (open-source models, runs on your machine).

Nothing leaves your computer: prompts go to a local Ollama server (default 127.0.0.1:11434).
If Ollama isn't running or has no model, `status()` says so and the app keeps using templates.

Safety rules baked in:
  * the model is given ONLY facts we hold about the business (name, niche, city, observed website
    issues) and the operator's own offer - never other leads' data;
  * it must not invent facts, prices, or claims, and must include an opt-out line;
  * the draft is returned for review / the approval queue. It is never sent automatically.
"""

from __future__ import annotations

import json
import re

from . import http

OLLAMA = "http://127.0.0.1:11434"
PREFERRED = ("llama3.2", "llama3.1", "qwen2.5", "mistral", "gemma2", "phi3")
BANNED = re.compile(r"guarantee|100%|risk[- ]free|act now|limited time|# ?1 ", re.I)


def pick_model(installed: list[str], wanted: str | None = None) -> str | None:
    if wanted and wanted in installed:
        return wanted
    for pref in PREFERRED:
        for m in installed:
            if m.startswith(pref):
                return m
    return installed[0] if installed else None


def status(base: str = OLLAMA) -> dict:
    try:
        r = http.get(f"{base}/api/tags", breaker="ollama", retries=0, timeout=3)
        models = [m["name"] for m in r.json().get("models", [])] if r.status_code < 400 else []
    except Exception:  # noqa: BLE001
        return {"available": False, "models": [], "reason": "Ollama isn't running. Install it from ollama.com, "
                "run `ollama serve`, then `ollama pull llama3.2`. Templates keep working meanwhile."}
    if not models:
        return {"available": False, "models": [], "reason": "Ollama is running but has no model. Run `ollama pull llama3.2`."}
    return {"available": True, "models": models, "default": pick_model(models)}


def build_prompt(lead: dict, offer: str, sender: str, channel: str = "email", tone: str = "friendly, concise") -> str:
    facts = [f"Business: {lead.get('business_name')}"]
    for key, label in (("niche", "Type"), ("city", "City"), ("owner_name", "Contact name")):
        if lead.get(key):
            facts.append(f"{label}: {lead[key]}")
    issues = lead.get("website_issues")
    if isinstance(issues, str):
        try:
            issues = json.loads(issues)
        except ValueError:
            issues = []
    if not lead.get("website_url"):
        facts.append("Observation: no website is listed for them.")
    elif issues:
        facts.append("Observation: their website " + str(issues[0]).rstrip(".").lower() + ".")
    limit = "under 90 words" if channel != "email" else "under 120 words"
    return (
        "Write a short first outreach message from a small web studio to a local business owner.\n"
        f"Tone: {tone}. Length: {limit}. Channel: {channel}.\n"
        f"Sender: {sender}\nWhat we offer: {offer}\n\nFacts about the business (use ONLY these):\n- "
        + "\n- ".join(facts) +
        "\n\nRules: do not invent facts, results, prices, awards or names. Mention one specific observation from the facts. "
        "One clear, low-pressure call to action. No hype, no guarantees. End with: \"If you'd rather not hear from me, just reply STOP.\"\n"
        + ("Return JSON: {\"subject\": \"...\", \"body\": \"...\"}" if channel == "email"
           else "Return JSON: {\"body\": \"...\"}")
    )


def parse_draft(raw: str, channel: str = "email") -> dict:
    """Pure: extract {subject, body} from model output; reject unsafe/garbage drafts."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    body = str(data.get("body") or "").strip()
    subject = str(data.get("subject") or "").strip()
    if not body and raw and not m:
        body = raw.strip()
    problems = []
    if len(body) < 40:
        problems.append("draft too short")
    if BANNED.search(body):
        problems.append("contains hype/guarantee language")
    if "stop" not in body.lower():
        body = body.rstrip() + "\n\nIf you'd rather not hear from me, just reply STOP."
    if channel == "email" and not subject:
        subject = "Quick idea for your business"
    return {"subject": subject[:150] if channel == "email" else None, "body": body[:2000], "problems": problems}


def draft(lead: dict, offer: str, sender: str, channel: str = "email", model: str | None = None,
          base: str = OLLAMA) -> dict:
    st = status(base)
    if not st["available"]:
        raise RuntimeError(st["reason"])
    use = pick_model(st["models"], model)
    r = http.post(f"{base}/api/generate", breaker="ollama", retries=0, timeout=120,
                  json={"model": use, "prompt": build_prompt(lead, offer, sender, channel), "stream": False,
                        "format": "json", "options": {"temperature": 0.4, "num_predict": 400}})
    if r.status_code >= 400:
        raise RuntimeError(f"Ollama error {r.status_code}: {r.text[:200]}")
    out = parse_draft(r.json().get("response", ""), channel)
    return {**out, "model": use, "ai_generated": True, "note": "Review before sending - AI can still get things wrong."}
