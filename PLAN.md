# The Plan: $200/Month, Two Engines

## The honest math first

$200/month sounds small but most side hustles fail because the math never worked.
Ours works because the numbers are stupidly forgiving:

- **One $300 website sale = 1.5 months of goal.** We need to close 1 client/month.
  At a realistic 2–5% close rate on cold outreach, that's 20–50 messages a month —
  about 10 a week, 15 minutes a day.
- **One decent flip = $30–80 profit.** Two or three flips a month backstops any
  slow service month.
- **The $10/mo "care plan"** (hosting + edits) compounds: every client you ever close
  keeps paying. By month 6, five past clients = $50/mo recurring before you lift a finger.

Neither engine needs to work perfectly. They only need to *combine* to $200.

## Budget: the $500

| Allocation | Amount | Notes |
|-----------|--------|-------|
| Flip inventory | $350 | Never spend it all on one item. $50–120 per flip, 3–5 items in rotation. |
| Domain + email | $15 | One domain for the studio (e.g. blankdweb.com). Hosting is $0 — GitHub Pages/Netlify free tier. |
| Reserve | $135 | Untouched in month 1. It's for doubling down on whatever works in month 2 (shipping supplies, a second domain, boosted post). |

**Rule: the reserve doesn't move until we have month-1 data.** Broke operations die
because they spend the reserve on hope.

## Engine 1: Blankd Web Studio

**The pitch:** *"Professional website for your business. $300 flat. Live in 48 hours. $10/month keeps it hosted and updated."*

**Why it sells:** local businesses don't compare you to Squarespace — they compare you
to the $2,500 agency quote they got scared by, or the nothing they currently have.
$300/48hrs is an easy yes.

**Your role vs. my role:**
- **Me:** every site gets built here, in this repo, by me. Demo sites, client sites,
  revisions — you paste the client's info and photos, I ship the site.
- **You:** find them, message them, take the call, collect payment (Stripe payment
  link, Venmo, Zelle — whatever they'll pay with), send me the details.

**The funnel:**
1. Google Maps → search a niche + your city ("barber", "detailing", "landscaping",
   "taco", "nail salon"). Open each one. No website, or a Facebook-page-as-website,
   or a site that's clearly ancient → they're a lead.
2. 10 outreach messages/week from `service/outreach/templates.md`. Track every send
   in the ledger's notes or a simple list.
3. Interested reply → send them your landing page + the demo closest to their niche.
   ("Here's one I did for a barbershop — yours would look like this with your branding.")
4. Close at $300. Get 50% up front. I build it same day. Deliver in 48h, collect the rest.
5. Offer the $10/mo care plan at delivery. Most say yes because they don't want to
   think about hosting.

**Month-1 target:** 40 messages sent, 1 client closed. That's it.

## Engine 2: FlipFinder

You already flip — this makes your capital smarter, not just busier.

**What the tool does** (`flipfinder/flipfinder.py`):
- Searches eBay for a query/category you know well
- Pulls live listings, computes the price distribution
- Flags listings priced well below the market median
- Does the full margin math: resale estimate − eBay fees (13.25% + $0.30) −
  shipping − your buy price = projected profit
- Ranks the best opportunities

**The discipline rules (these matter more than the tool):**
1. Only flip categories you can judge condition on from photos.
2. Minimum $25 projected profit per flip, or skip it.
3. $120 max on a single item in month 1.
4. Money from a sale goes: profit → ledger, principal → back into inventory.

**Month-1 target:** 3 flips completed, ~$100 total profit. Conservative on purpose.

## Week-by-week, month 1

| Week | Service | Flips |
|------|---------|-------|
| 1 | Deploy landing page + demos. Buy domain. Build lead list of 25 businesses. Send first 10 messages. | Get free eBay dev keys, run FlipFinder for real, buy flip #1. |
| 2 | 10 more messages. Follow up on week-1 sends (follow-ups close more than first touches). | List flip #1. Buy flip #2. |
| 3 | 10 more messages + follow-ups. First close likely lands here. | Flip #1 sells. Buy #3. |
| 4 | Deliver client site. Pitch care plan. 10 more messages. | Keep the rotation moving. |

## Scorecard (what you critique me on in month 2)

Every dollar goes in `tracker/ledger.csv`. Run `python3 tracker/summary.py` for the P&L.

- **Revenue target:** $200 (stretch: $400 = 1 site + 3 flips)
- **Leading indicators if revenue misses:** messages sent, reply rate, flips listed.
  If the *inputs* happened and revenue didn't, we fix the pitch/pricing. If the inputs
  didn't happen, that's the real problem — and it's fixable too.

## What we deliberately are NOT doing

- Trading/crypto with the $500 — negative expected value for us, pure variance.
- Dropshipping — race to the bottom, ad spend eats $500 for breakfast.
- "Passive income" products first — real, but a 3-month ramp. Month 3 project, funded by this.
