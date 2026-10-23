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
About us: http://arthvex.co.in/

Would a 15-min call make sense to see if we're a fit?

{sender_name}
{sender_company}
http://arthvex.co.in/ | contact@arthvex.co.in""",
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
{sender_company}
http://arthvex.co.in/ | contact@arthvex.co.in""",
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
{sender_company}
http://arthvex.co.in/ | contact@arthvex.co.in""",
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
{sender_company}
http://arthvex.co.in/ | contact@arthvex.co.in""",
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
Would it be alright if I sent across a couple of examples?
More about us: http://arthvex.co.in/""",
    ),
    dict(
        name="whatsapp_follow_up",
        type="whatsapp",
        stage="day3",
        target_market="india",
        subject="",
        body="""Hi {owner_name}, just following up on my earlier message about a website
for {business_name}. Happy to share a quick before/after of similar work if
that's useful.
http://arthvex.co.in/""",
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
- Mention our site: http://arthvex.co.in/ and email contact@arthvex.co.in
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
{sender_company}
http://arthvex.co.in/ | contact@arthvex.co.in""",
    ),
    dict(
        name="cold_email_value_first",
        type="email",
        stage="first",
        target_market="international",
        subject="A free website audit for {business_name}",
        body="""Hi {owner_name},

I'm {sender_name} from {sender_company}. I looked at {business_name} and noticed
{observation}. For {niche} businesses in {city}, that often means missed enquiries.

I'd be happy to send a free 1-page audit with 3 quick wins - no obligation.
Want me to send it over?

{sender_name}
{sender_company}
http://arthvex.co.in/ | contact@arthvex.co.in""",
    ),
    dict(
        name="cold_email_no_website",
        type="email",
        stage="first",
        target_market="international",
        subject="{business_name} - found you on Google Maps, but no website",
        body="""Hi {owner_name},

People searching for {niche} in {city} find {business_name} on Google Maps, but
there's no website to turn them into customers.

We build a clean, mobile-first site in about 7 days, with a call button,
directions and your services. Examples: {portfolio_url}

Worth a quick chat?

{sender_name}
{sender_company}
http://arthvex.co.in/ | contact@arthvex.co.in""",
    ),
    dict(
        name="cold_email_redesign",
        type="email",
        stage="first",
        target_market="international",
        subject="Ideas to refresh {business_possessive} website",
        body="""Hi {owner_name},

I visited the {business_name} website and spotted something worth fixing:
{website_issue}.

We redesign sites to load faster, look great on phones and bring in more calls.
Here's recent work: {portfolio_url}

Open to a 15-min call this week?

{sender_name}
{sender_company}
http://arthvex.co.in/ | contact@arthvex.co.in""",
    ),
    dict(
        name="cold_email_seo_local",
        type="email",
        stage="first",
        target_market="international",
        subject="Getting {business_name} to show up for '{niche} {city}'",
        body="""Hi {owner_name},

When someone searches "{niche} {city}", is {business_name} showing up? We help
local businesses rank with a fast website and a tuned Google Business profile.

Happy to share a short report on where you stand today - free.

{sender_name}
{sender_company}
http://arthvex.co.in/ | contact@arthvex.co.in""",
    ),
    dict(
        name="follow_up_value_add",
        type="email",
        stage="day5",
        target_market="international",
        subject="One idea for {business_name}",
        body="""Hi {owner_name},

Quick idea since I didn't hear back: a simple "Call now" and WhatsApp button
above the fold on your homepage typically lifts enquiries for {niche} businesses.

Glad to mock it up for {business_name} at no cost.

{sender_name}
{sender_company}
http://arthvex.co.in/ | contact@arthvex.co.in""",
    ),
    dict(
        name="meeting_request_email",
        type="email",
        stage="meeting",
        target_market="international",
        subject="15 minutes this week, {owner_name}?",
        body="""Hi {owner_name},

Could we do a quick 15-min call this week to talk about the {business_name}
website? I'll bring two concrete ideas. Suggest a time that suits you and I'll
send an invite.

{sender_name}
{sender_company}
http://arthvex.co.in/ | contact@arthvex.co.in""",
    ),
    dict(
        name="thank_you_after_call",
        type="email",
        stage="post_call",
        target_market="international",
        subject="Thanks for your time, {owner_name}",
        body="""Hi {owner_name},

Thanks for speaking with me today. As discussed, I'll send a short proposal for
{business_name} shortly. If you have photos, a logo or a list of services, send
them across and we can start straight away.

{sender_name}
{sender_company}
http://arthvex.co.in/ | contact@arthvex.co.in""",
    ),
    dict(
        name="referral_request_email",
        type="email",
        stage="referral",
        target_market="international",
        subject="Know anyone who needs a website?",
        body="""Hi {owner_name},

It was great working with {business_name}. If you know another business owner who
could use a fast, modern website, I'd appreciate an introduction - and I'll look
after them well.

{sender_name}
{sender_company}
http://arthvex.co.in/ | contact@arthvex.co.in""",
    ),
    dict(
        name="whatsapp_no_website",
        type="whatsapp",
        stage="first",
        target_market="india",
        subject="",
        body="""Hi {owner_name}, this is {sender_name} from {sender_company}. I saw
{business_name} on Google Maps but couldn't find a website. We make a simple,
mobile-friendly site for {niche} businesses in {city} within 7 days.
Shall I send some samples? http://arthvex.co.in/""",
    ),
    dict(
        name="whatsapp_offer",
        type="whatsapp",
        stage="day5",
        target_market="india",
        subject="",
        body="""Hi {owner_name}, {sender_name} here from {sender_company}. This month we're
offering a ready-to-go business website for {niche} shops in {city} at a special
launch price. Want the details? Reply YES. http://arthvex.co.in/""",
    ),
    dict(
        name="whatsapp_meeting",
        type="whatsapp",
        stage="meeting",
        target_market="india",
        subject="",
        body="""Hi {owner_name}, can we do a quick 10-min call today or tomorrow about
{business_name}'s website? Tell me a time that works. - {sender_name}, {sender_company}
(http://arthvex.co.in/ | contact@arthvex.co.in)""",
    ),
    dict(
        name="whatsapp_thank_you",
        type="whatsapp",
        stage="post_call",
        target_market="india",
        subject="",
        body="""Thanks for your time, {owner_name}! I'll share the proposal for
{business_name} shortly. Details about our work: http://arthvex.co.in/ -
{sender_name}, {sender_company}""",
    ),
    dict(
        name="call_script_follow_up",
        type="call_script",
        stage="day3",
        target_market="india",
        subject="",
        body="""OPENER
"Hi {owner_name}, {sender_name} from {sender_company} - I messaged you about a
website for {business_name}. Do you have a minute?"

IF YES
"I can show you a quick sample for a {niche} business in {city}. What's the best
WhatsApp number? I'll also email details from contact@arthvex.co.in."

IF BUSY
"No problem - what time suits you for a 5-minute call?"

END WITH
- Mention http://arthvex.co.in/
- Log the outcome in the CRM""",
    ),
    dict(
        name="call_script_objections",
        type="call_script",
        stage="objections",
        target_market="india",
        subject="",
        body=""""WE ALREADY HAVE A WEBSITE / FACEBOOK PAGE"
"Great - ours would sit alongside it and rank on Google for {niche} in {city}."

"TOO EXPENSIVE"
"Fair. Plans start small and I can scope just the essentials for {business_name}."

"NOT NOW"
"Understood. May I send examples so you have them later?"

"SEND DETAILS"
"Sure - on WhatsApp or email? Our site is http://arthvex.co.in/ and I'm at
contact@arthvex.co.in." """,
    ),
]


def seed_templates() -> None:
    """Seed the built-in templates into the current workspace."""
    from .db import current_ws

    workspace_id = current_ws()
    for tpl in TEMPLATES:
        execute(
            """INSERT IGNORE INTO templates
               (workspace_id, name, type, stage, subject, body, language, target_market,
                created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                workspace_id,
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
