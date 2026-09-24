"""Default outreach templates (Part 2.3 / 3.3).

Merge fields available: {business_name} {owner_name} {city} {niche}
{observation} {website_issue} {portfolio_url} {sender_name} {sender_company}
"""

from __future__ import annotations

from .db import execute, utcnow

TEMPLATES = [
    dict(
        name="cold_email_first",
        type="email",
        stage="first",
        target_market="international",
        subject="Quick thought about {business_possessive} website",
        body="""Hi {owner_name},

I came across {business_name} on Google Maps and noticed {observation}.

I run a small web studio - we specialise in building modern, fast websites for
{niche} businesses. Typical turnaround is 7 days from kickoff to launch.

Here's an example of similar work: {portfolio_url}

Would a 15-min call make sense to see if we're a fit?

{sender_name}
{sender_company}""",
    ),
    dict(
        name="follow_up_day_3",
        type="email",
        stage="day3",
        target_market="international",
        subject="Re: {business_possessive} website",
        body="""Hi {owner_name},

Just bumping this up in case it got buried. Happy to send over a couple of
quick ideas for {business_name} either way - no strings.

{sender_name}
{sender_company}""",
    ),
    dict(
        name="follow_up_day_7",
        type="email",
        stage="day7",
        target_market="international",
        subject="3 things I'd change on {business_possessive} site",
        body="""Hi {owner_name},

I had a proper look at your site. Three things I'd fix first:

1. {website_issue}
2. Mobile layout - most of your traffic is on a phone
3. A clear "book / call now" action above the fold

Happy to walk you through it on a 15-min call. If it's not the right time,
just say the word and I'll stop emailing.

{sender_name}
{sender_company}""",
    ),
    dict(
        name="follow_up_day_14",
        type="email",
        stage="day14",
        target_market="international",
        subject="Closing the loop on {business_name}",
        body="""Hi {owner_name},

No worries if the timing isn't right - I'll close the loop here.

If anything changes and you want a fast, modern site for {business_name},
you know where to find me.

{sender_name}
{sender_company}""",
    ),
    dict(
        name="whatsapp_first",
        type="whatsapp",
        stage="first",
        target_market="india",
        subject="",
        body="""Hi {owner_name}, this is {sender_name} from {sender_company}. I found
{business_name} on Google Maps and noticed {observation}. We build fast,
mobile-friendly websites for {niche} businesses in {city} in about 7 days.
Would it be alright if I sent across a couple of examples?""",
    ),
    dict(
        name="whatsapp_follow_up",
        type="whatsapp",
        stage="day3",
        target_market="india",
        subject="",
        body="""Hi {owner_name}, just following up on my earlier message about a website
for {business_name}. Happy to share a quick before/after of similar work if
that's useful.""",
    ),
    dict(
        name="cold_call_script",
        type="call_script",
        stage="first",
        target_market="india",
        subject="",
        body="""OPENER
"Hi, is this {owner_name} from {business_name}? I'll be quick - I build
websites for {niche} businesses here in {city}. I noticed {observation}."

PAUSE. Let them respond.

IF INTERESTED
"What normally happens is I put together a 5-page site - photos, menu/services,
directions, a call button - live in about 7 days. Can I send you two examples
on WhatsApp?"

IF "WE'RE FINE"
"Totally fair. Can I send one example anyway, so you have it if you ever need it?"

ALWAYS END WITH
- Confirm the best WhatsApp number
- Log the call outcome in the CRM before you dial the next one""",
    ),
    dict(
        name="proposal_email",
        type="email",
        stage="proposal",
        target_market="international",
        subject="Proposal - {business_name} website",
        body="""Hi {owner_name},

Great speaking with you. Here's what I'm proposing for {business_name}:

SCOPE
- 5-7 page website, mobile-first, built for speed
- Contact / booking flow wired to your phone and email
- Basic on-page SEO for "{niche} {city}"
- Copy and image placement handled by us from what you send over

TIMELINE   7 working days from kickoff
INVESTMENT [amount] - 50% to start, 50% on launch
AFTER      Optional maintenance at [amount]/month

Happy to adjust anything here. If it looks right, reply "go" and I'll send the
kickoff checklist today.

{sender_name}
{sender_company}""",
    ),
]


def seed_templates() -> None:
    for tpl in TEMPLATES:
        execute(
            """INSERT IGNORE INTO templates
               (name, type, stage, subject, body, language, target_market, created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                tpl["name"],
                tpl["type"],
                tpl["stage"],
                tpl["subject"],
                tpl["body"],
                "en",
                tpl["target_market"],
                utcnow(),
            ),
        )
