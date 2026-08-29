# The $200/Month Operation

Two income engines, one repo, one goal: **$200+/month net by the end of month 2.**

| Engine | What it is | Target |
|--------|-----------|--------|
| 🌐 **Blankd Web Studio** (`service/`) | Flat-rate $300 websites for local businesses, delivered in 48 hours | 1 sale/month = goal beaten |
| 📦 **FlipFinder** (`flipfinder/`) | eBay deal-scanner that finds underpriced listings and does the margin math | Turn ~$350 of capital into data-backed flips |

Read **[PLAN.md](PLAN.md)** for the full strategy, budget, and week-by-week playbook.

## Third engine: AI Trading Brain (`brain/`)

A quantitative trading research and decision-support system built around the
SAR / Morgan Trades momentum breakout methodology. Formalises qualitative
trading rules into testable definitions, backtests them without look-ahead,
and reports evidence honestly — including when the evidence contradicts the
source. Not a signal service; a research apparatus.

```
cd brain && python3 -m pip install -r requirements.txt
python3 -m tradingbrain.cli seed && python3 -m tradingbrain.cli serve
```

Read **[brain/README.md](brain/README.md)** — it explains the evidence-class
system, what is deliberately not built, and how to feed it real market data
(it ships on a clearly-labelled synthetic generator because this environment
has no market-data network access).

## Repo map

```
PLAN.md                     The business plan — start here
service/
  site/index.html           Your sales landing page (deploy free on GitHub Pages/Netlify)
  demos/                     Demo sites: barber, detailing, taqueria, landscaping
  outreach/templates.md      Cold DM/email/text scripts + who to target + follow-up cadence
  outreach/leads.csv         Your lead list — every business you find goes here
  outreach/pipeline.py       Run daily: tells you exactly who to message/follow up today
  INTAKE.md                  Checklist for when a client says yes
flipfinder/
  flipfinder.py              The deal scanner (runs in --mock mode with zero setup)
  watchlist.json             Your saved searches — scan them all with --watchlist
  README.md                  Setup guide incl. free eBay developer keys
tracker/
  ledger.csv                 Every dollar in/out gets a row
  summary.py                 Run it to see monthly P&L
```

## Your first 3 moves (under 1 hour total)

1. **Deploy the landing page free** — GitHub Pages or Netlify drop, instructions in `service/README.md`. Domain (~$12) optional but recommended.
2. **Run the flip tool right now**: `python3 flipfinder/flipfinder.py --mock` — see how it thinks before you get API keys.
3. **Send your first 5 outreach messages** using `service/outreach/templates.md`. Volume is the whole game.
