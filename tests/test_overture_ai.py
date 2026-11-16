"""Pure-logic tests: Overture mapping, local-AI prompt/guardrails."""
from app import aidraft
from app.providers import overture


def test_overture_candidate_mapping():
    row = {"id": "abc", "name": "Dental Care", "confidence": 0.39, "operating_status": None,
           "website": "http://wa.me/919158999374", "phone": "+912024223120", "email": "x@clinic.in",
           "social": "https://www.facebook.com/104112789273121", "addr": "Apte Rd", "city": "Pune",
           "pin": "411004", "lat": 18.52, "lng": 73.84}
    c = overture.to_candidate(row, "Pune", "India")
    assert c["place_id"] == "overture:abc" and c["website_url"] is None and c["whatsapp"] == "919158999374"
    assert c["facebook_url"].endswith("104112789273121") and "39% confidence" in c["source"]
    assert overture.to_candidate({"id": "z", "name": ""}, "x", "") is None
    closed = overture.to_candidate({**row, "operating_status": "closed"}, "Pune", "")
    assert closed["business_status"] == "CLOSED_PERMANENTLY"
    web = overture.classify_links("https://www.smile.in/", ["https://instagram.com/smile"])
    assert web["website_url"] == "https://www.smile.in/" and web["instagram_url"].endswith("/smile")


def test_bbox_contains_centre():
    xmin, xmax, ymin, ymax = overture.bbox_around(18.5, 73.8, 3000)
    assert xmin < 73.8 < xmax and ymin < 18.5 < ymax and (ymax - ymin) < 0.1


def test_model_pick():
    assert aidraft.pick_model(["zzz:1b", "llama3.2:3b"]) == "llama3.2:3b"
    assert aidraft.pick_model(["a", "b"], "b") == "b" and aidraft.pick_model([]) is None


def test_prompt_uses_only_given_facts_and_rules():
    lead = {"business_name": "Smile Dental", "niche": "dentist", "city": "Pune", "website_url": "",
            "owner_name": "Dr. Rao", "email": "secret@x.in", "phone": "9999999999"}
    p = aidraft.build_prompt(lead, "websites for clinics", "Pixel Forge")
    assert "Smile Dental" in p and "no website is listed" in p and "STOP" in p and "do not invent" in p.lower()
    assert "secret@x.in" not in p and "9999999999" not in p        # contact details never go in the prompt


def test_parse_draft_guardrails():
    good = aidraft.parse_draft('{"subject":"Quick idea","body":"Hi Dr Rao, I noticed Smile Dental has no website listed. We build simple sites for clinics. Open to a quick chat?"}')
    assert good["subject"] == "Quick idea" and "STOP" in good["body"] and not good["problems"]
    hype = aidraft.parse_draft('{"subject":"x","body":"We GUARANTEE 100% more patients, act now! Reply today for a limited time deal on your site."}')
    assert any("hype" in p for p in hype["problems"])
    assert aidraft.parse_draft("garbage", "email")["problems"] == ["draft too short"]
    assert aidraft.parse_draft('{"body":"A short enough message that is long enough to count as a real draft for a WhatsApp chat."}', "whatsapp")["subject"] is None
