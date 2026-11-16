"""Pure-logic tests: reply intelligence, pacing, eligibility, vCard (no database)."""
from datetime import datetime, timezone

import pytest

from app import eligibility, pacing, replies
from app import exportutil


@pytest.mark.parametrize("text,intent", [
    ("Please STOP emailing me", "STOP"),
    ("Remove me from your list.", "STOP"),
    ("Don't contact me again", "STOP"),
    ("I am out of office until Monday", "AUTO_REPLY"),
    ("Not interested, thanks", "NOT_INTERESTED"),
    ("We already have a website made", "NOT_INTERESTED"),
    ("Maybe later, busy right now", "NOT_NOW"),
    ("Can we have a call tomorrow at 5pm?", "WANTS_MEETING"),
    ("Let's schedule a call", "WANTS_MEETING"),
    ("How much would this cost?", "ASKS_PRICE"),
    ("Sounds good, tell me more", "INTERESTED"),
    ("Haan zaroor, details bhejo", "INTERESTED"),
    ("Tidak tertarik, terima kasih", "NOT_INTERESTED"),
    ("Berapa harga paketnya?", "ASKS_PRICE"),
    ("ok", "UNCLEAR"),
    ("", "UNCLEAR"),
])
def test_intents(text, intent):
    assert replies.classify(text)["intent"] == intent


def test_not_interested_beats_interested_and_stop_beats_all():
    assert replies.classify("I'm not interested")["intent"] == "NOT_INTERESTED"
    assert replies.classify("interested? no. stop emailing")["intent"] == "STOP"


def test_quoted_history_is_ignored():
    text = "Sounds good, send details\n\nOn Mon, 3 Nov 2026 at 10:00, Sales wrote:\n> reply STOP to unsubscribe"
    assert replies.classify(text)["intent"] == "INTERESTED"
    assert replies.classify("> please stop\nthanks, yes")["intent"] == "INTERESTED"


def test_referral_extracts_contact_and_needs_detail():
    r = replies.classify("Please contact our manager Rahul Sharma at rahul@acme.in or +91 98765 43210")
    assert r["intent"] == "REFERRAL"
    assert r["referral"]["emails"] == ["rahul@acme.in"] and r["referral"]["phones"][0].endswith("9876543210")
    assert replies.classify("wrong person")["intent"] != "REFERRAL"      # no contact detail -> not actionable


def test_not_now_sets_defer():
    assert replies.classify("Not now, reach out next week")["defer_days"] == 7
    assert replies.classify("maybe later")["defer_days"] == 30


def test_transitions_forward_only_and_won_is_sacred():
    assert replies.next_status("CONTACTED", "REPLIED") == "REPLIED"
    assert replies.next_status("MEETING_SET", "REPLIED") is None
    assert replies.next_status("WON", "LOST") is None
    assert replies.next_status("NEW", "LOST") == "LOST"
    assert replies.next_status("LOST", "REPLIED") == "REPLIED"          # revived
    assert replies.next_status("CONTACTED", None) is None
    assert replies.ACTIONS["INTERESTED"]["status"] != "WON"


def test_stop_suppresses_and_cancels_but_autoreply_does_not():
    assert replies.ACTIONS["STOP"]["suppress"] and replies.ACTIONS["STOP"]["cancel"]
    assert not replies.ACTIONS["AUTO_REPLY"]["cancel"] and replies.ACTIONS["AUTO_REPLY"]["status"] is None


UTC = timezone.utc
KW = dict(hourly_cap=20, daily_cap=100, quiet_start=21, quiet_end=8, tz_name="Asia/Kolkata")


def test_quiet_hours_cross_midnight():
    assert pacing.in_quiet_hours(datetime(2026, 11, 3, 18, 0, tzinfo=UTC), 21, 8, "Asia/Kolkata")   # 23:30 IST
    assert not pacing.in_quiet_hours(datetime(2026, 11, 3, 6, 0, tzinfo=UTC), 21, 8, "Asia/Kolkata")  # 11:30 IST
    assert not pacing.in_quiet_hours(datetime(2026, 11, 3, 3, 0, tzinfo=UTC), 5, 5, "UTC")           # disabled


def test_send_decision_caps_and_reasons():
    day = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)                        # Tue 11:30 IST
    assert pacing.send_decision(day, 5, 10, **KW) == {"allowed": True, "budget": 15, "reason": "ok"}
    assert pacing.send_decision(day, 0, 95, **KW)["budget"] == 5
    assert "daily cap" in pacing.send_decision(day, 0, 100, **KW)["reason"]
    assert "hourly cap" in pacing.send_decision(day, 20, 20, **KW)["reason"]
    night = datetime(2026, 11, 3, 18, 0, tzinfo=UTC)
    assert not pacing.send_decision(night, 0, 0, **KW)["allowed"]
    sat = datetime(2026, 11, 7, 6, 0, tzinfo=UTC)
    assert not pacing.send_decision(sat, 0, 0, weekdays_only=True, **KW)["allowed"]
    assert pacing.send_decision(day, 0, 0, hourly_cap=20, daily_cap=0, quiet_start=0, quiet_end=0)["budget"] == 20


def test_bad_timezone_falls_back_and_jitter_in_range():
    assert pacing.in_quiet_hours(datetime(2026, 1, 1, 23, tzinfo=UTC), 21, 8, "Not/AZone")
    assert all(20 <= pacing.jitter_seconds(20, 90) <= 90 for _ in range(50))
    assert 5 <= pacing.jitter_seconds(9, 5) <= 9


def test_eligibility():
    ok = {"email": "a@b.in", "email_valid": 1, "status": "NEW"}
    assert eligibility.outreach_eligibility(ok)["eligible"]
    assert not eligibility.outreach_eligibility({**ok, "business_status": "CLOSED_PERMANENTLY"})["eligible"]
    assert not eligibility.outreach_eligibility({**ok, "status": "LOST"})["eligible"]
    assert not eligibility.outreach_eligibility(ok, suppressed=True)["eligible"]
    assert not eligibility.outreach_eligibility({**ok, "email_valid": 0})["eligible"]
    assert not eligibility.outreach_eligibility({"status": "NEW"})["eligible"]
    assert eligibility.outreach_eligibility({"whatsapp": "919876543210", "status": "NEW"}, "whatsapp")["eligible"]


def test_vcard_escaping_and_structure():
    out = exportutil.to_vcard([{"business_name": "Smith, Jones; & Co", "phone": "9876543210", "email": "a@b.in",
                                "address": "1 Rd", "city": "Pune", "status": "NEW", "lead_score": 7}, {"business_name": ""}])
    assert out.count("BEGIN:VCARD") == 1 and out.count("END:VCARD") == 1
    assert r"FN:Smith\, Jones\; & Co" in out and "TEL;TYPE=WORK,VOICE:9876543210" in out
    assert "ADR;TYPE=WORK:;;1 Rd;Pune;" in out and "\r\n" in out
    assert exportutil.to_vcard([]) == ""
