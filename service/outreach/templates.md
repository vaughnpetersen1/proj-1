# Outreach Playbook — Blankd Web Studio

Volume + follow-up wins. Personalization beats cleverness. Every message must name
something specific about *their* business — that one detail doubles reply rates.

## Who to target (build a list of 25 before sending anything)

Google Maps → "[niche] near me". Good niches: barbers, detailers, landscapers,
cleaners, taco/food spots, nail salons, tattoo artists, handymen, mobile mechanics.

A business is a **lead** only if it's ACTIVE **and** one of these web-presence
problems is true:

**Activity gate (check FIRST — boss rule, tightened 2026-07-23):** the business
must be PROVABLY active within the last 12 months. Claude cannot see FB/IG feeds
(proxy blocks them), so "has a Facebook page" is NOT proof. Proof = a dated
signal Claude can actually find: a Google/Yelp review dated within ~12 months, a
dated recent post in search, or a listing "updated [month] 2026" with a real
date. No dated proof found → it goes on the BENCH ("verify feed yourself"), NOT
the send list. Better to hand over 2 proven-active leads than 6 guesses.

Then, if proven active, it's a lead when EITHER is true (boss, 2026-07-23 — we
stay small-business for now, but both no-site and bad-site count):
- NO website: nothing but a Facebook page, a Linktree, or no link at all
- SHITTY site: broken on mobile, no prices/hours, © 2017 or older, ugly/dated
  → this is a REDESIGN pitch ("modernize your site"), not "you have none"

Skip the big established industries (roofing, HVAC, dentists, med spas) — they
almost all have real sites and gatekeepers; wrong fit for cold DMs right now.

Log each lead: name, niche, phone/IG/email, no-site vs bad-site, AND the dated
activity signal that proves they're alive (e.g., "Yelp review 03/2026").

## Message 1 — Instagram/Facebook DM (best reply rates for local)

> Hey! Found [Business Name] on Google Maps while looking at [niche]s in the area —
> your work looks great, but I noticed you don't have a website (just the [FB page/IG]).
> I build sites for local businesses: $300 flat, live in 48 hours, and it makes you
> show up better when people search "[niche] near me."
>
> Here's one I did for a [their niche or closest]: [demo link]
>
> Want me to mock up what yours would look like? No charge for the mockup.

**Why it works:** specific ("found you on Maps"), cheap, fast, zero-risk ask at the end.

## Message 2 — Email version

Subject: **quick question about [Business Name]'s website**

> Hi [Name],
>
> I was looking up [niche]s in [city] and [Business Name] came up — solid reviews,
> but no website, which means you're losing the people who Google before they call.
>
> I build websites for local businesses at a flat $300, delivered in 48 hours,
> with an optional $10/mo to keep it hosted and updated so you never think about it.
>
> Here's a recent example: [demo link]
>
> If you're interested, just reply with a sentence about your business and I'll
> send back a free mockup of your homepage. If not, no worries at all.
>
> [Your name]
> Blankd Web Studio

## Message 3 — Text/SMS (when only a phone number is listed)

> Hi, is this [Business Name]? I help local [niche]s get websites — $300 flat,
> done in 48hrs. Noticed you don't have one and you're probably losing Google
> traffic to [competitor with a site]. Example of my work: [demo link].
> Want a free mockup? — [Your name]

## The follow-up (where most of the money is)

**3–4 days after message 1, no reply:**
> Hey [Name], following up — I went ahead and sketched what a homepage for
> [Business Name] could look like: [screenshot]. If you like the direction,
> it's $300 and it's live this week. If not, keep the design idea on me. 👍

*(Ping me — I'll generate a real mockup for any lead you want to follow up on.
A screenshot of THEIR business name on a beautiful homepage is the single
highest-converting thing you can send.)*

**One week later, final touch:**
> Last note from me — booking slots for next week and wanted to give you first
> shot before I fill them. Either way, good luck with [Business Name]! 🙌

Then stop. Three touches, done. They go on the "re-ping in 3 months" list.

## When they reply interested

1. Answer fast (within the hour if you can).
2. Ask: "What do you want people to DO on the site — call, book, or find you?"
3. Quote: **$300 flat, half up front, live in 48 hours after I get your info.**
4. Collect: business info, services + prices, hours, photos, logo if any.
5. Paste all of it to me in a session → I build → you deliver → collect the rest.
6. At delivery: "Want me to handle hosting and any future edits for $10/mo? Most
   clients do it so they never have to touch it." 

## Objection cheat sheet

- **"I get all my business from word of mouth."** → "Totally — the site's not to
  replace that, it's so when someone gets referred to you and Googles your name,
  you look as good online as your work is."
- **"$300 is a lot right now."** → "I can do $150 now, $150 when it's live and
  you love it. You only finish paying when you're happy."
- **"I have a Facebook page."** → "FB only shows your stuff to people already
  following you. A site catches the people searching '[niche] near me' who've
  never heard of you yet."
- **"Let me think about it."** → "Of course. I'll send you a free mockup of your
  homepage tomorrow so you're deciding on something real instead of imagining it."

## Weekly cadence (15 min/day)

- **Mon:** add 6+ new leads to the list
- **Tue–Thu:** send 3–4 first-touch messages/day
- **Fri:** send all due follow-ups
- **Always:** log sends + replies so we can compute the actual close rate at month-end

---

## The lead machine (added 2026-07-10)

Two scripts now automate the grunt work:

1. **`leadfinder.py`** — searches Google Maps (official Places API) for a
   niche + city and keeps only businesses with NO website on their profile.
   `python3 leadfinder.py "barbershop in Duluth MN"` → found_leads.csv

   **API key setup (~10 min, free at our volume):**
   - console.cloud.google.com → create project → enable "Places API (New)"
   - Credentials → Create API key → `export GOOGLE_PLACES_API_KEY=...`
   - Google's free monthly credit covers thousands of searches; we use dozens.

2. **`generate_messages.py`** — merges every found lead into the right
   niche template and writes ready-to-paste messages to outbox.md.

**What stays human, on purpose:** the 30-second verify per lead, and YOUR
thumb on the send button. No auto-sending — automated DM blasts violate
platform rules, risk the (paid, verified) account, and convert worse than
the personal touch that's already getting replies.
