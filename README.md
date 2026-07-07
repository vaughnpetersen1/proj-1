# The $200/Month Operation

Two income engines, one repo, one goal: **$200+/month net by the end of month 2.**

| Engine | What it is | Target |
|--------|-----------|--------|
| 🌐 **Blankd Web Studio** (`service/`) | Flat-rate $300 websites for local businesses, delivered in 48 hours | 1 sale/month = goal beaten |
| 📦 **FlipFinder** (`flipfinder/`) | eBay deal-scanner that finds underpriced listings and does the margin math | Turn ~$350 of capital into data-backed flips |

Read **[PLAN.md](PLAN.md)** for the full strategy, budget, and week-by-week playbook.

## Repo map

```
PLAN.md                     The business plan — start here
service/
  site/index.html           Your sales landing page (deploy free on GitHub Pages/Netlify)
  demos/barber.html          Demo site #1 — barbershop
  demos/detailing.html       Demo site #2 — auto detailing
  outreach/templates.md      Cold DM/email/text scripts + who to target + follow-up cadence
flipfinder/
  flipfinder.py              The deal scanner (runs in --mock mode with zero setup)
  README.md                  Setup guide incl. free eBay developer keys
tracker/
  ledger.csv                 Every dollar in/out gets a row
  summary.py                 Run it to see monthly P&L
```

## Your first 3 moves (under 1 hour total)

1. **Deploy the landing page free** — GitHub Pages or Netlify drop, instructions in `service/README.md`. Domain (~$12) optional but recommended.
2. **Run the flip tool right now**: `python3 flipfinder/flipfinder.py --mock` — see how it thinks before you get API keys.
3. **Send your first 5 outreach messages** using `service/outreach/templates.md`. Volume is the whole game.
