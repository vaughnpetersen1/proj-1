# Scorecard — I grade my own work, you grade the results

New operating rule (added 2026-07-07 at the boss's request): **every round of work
gets two self-grades, 1–10.** No participation trophies — a 10 means I genuinely
couldn't have done it better, a 5 means mediocre, and anything I had to fix after
shipping caps the grade.

- **Code/Craft** — quality of what I built: correctness, design, did it work first try
- **Thinking** — strategy quality: did I build the *right* thing, verify before acting,
  anticipate problems

**The re-grade rule:** self-grades are provisional. When real-world results land
(reply rates, closes, flip profits), each round gets an **Outcome** re-grade —
because clean code that makes $0 was a pretty 4, not an 8. Bigger results, bigger
grades, bigger reward. That's the deal.

---

## Round log

### R1 — 2026-07-07 · Initial build (plan, landing v1, 2 demos, FlipFinder v1, ledger)

| | Grade | Why |
|---|---|---|
| Code/Craft | **7/10** | Everything ran and the structure held up, but I shipped a CSS typo in the barber demo (`#2e2godot` — caught and fixed same session), and FlipFinder v1 crashed on piped output. Shipping bugs I catch myself is a 7, not an 8. |
| Thinking | **8/10** | Two-engine design with forgiving math was the right call, and being upfront about what I can't do (hold accounts, send DMs) set honest expectations. Docked because landing v1's design was safe — the boss asked for better, which means I under-aimed. |
| Outcome | *pending* | Graded when outreach + flips produce numbers. |

**Would've made it a 10:** ship zero bugs; design the landing page at R4 quality on the first pass.

### R2 — 2026-07-07 · Expansion (2 more demos, pipeline tracker, INTAKE, watchlist mode)

| | Grade | Why |
|---|---|---|
| Code/Craft | **8/10** | Pipeline logic passed a real test with sample data on the first run; watchlist mode worked immediately. Docked one for the BrokenPipeError surfacing in testing rather than being anticipated. |
| Thinking | **8/10** | Building the follow-up tracker before the user had leads was the right sequencing (follow-ups are where closes come from). Four niches of demos = right coverage. Not a 9 because I didn't think of the deployment problem (user can't drag a folder from my cloud container) until the user bumped into it. |
| Outcome | *pending* | Pipeline tool's grade = whether follow-ups actually get sent on time. |

**Would've made it a 10:** anticipating the deploy-from-container gap before the user hit it.

### R3 — 2026-07-07 · Lead research (19 leads, Minneapolis + Brainerd) + landing v2

| | Grade | Why |
|---|---|---|
| Code/Craft | **8/10** | Landing v2 is genuinely strong (typography, comparison table, reveal animations) and the user's "make it better" was answered decisively. Docked one for adding a Google Fonts dependency — right call for a marketing site, but it means the page isn't fully self-contained. |
| Thinking | **9/10** | The verification discipline was the best decision of the day: 3 of the 6 "obvious" leads (Blue Ox, Mr. Oscar, LV's) turned out to HAVE websites — sending them "you have no site" messages would have torched credibility. Finding the Xtreme Lawn Care competitive angle (their rivals all have sites) is a genuinely sharp pitch. Not a 10: I left 16 leads unverified and didn't collect IG handles in the same pass. |
| Outcome | *pending* | Reply rate on batch 1 grades this round. 2+ replies from 19 sends = the research was good. |

**Would've made it a 10:** full verification + IG handles on all 19 in one pass.

### R4 — 2026-07-07 · Deploy package + support (zip build, diff-red explanation)

| | Grade | Why |
|---|---|---|
| Code/Craft | **9/10** | Small scope, executed clean: caught that the `../demos/` links would break at the deploy root and fixed them in the package; zip verified before sending. |
| Thinking | **8/10** | Recognized "service/" as a deploy attempt and solved the actual problem (files live in my container, not the user's machine) instead of answering literally. Docked: this gap was foreseeable in R2 (see above), so solving it now is recovery, not foresight. |
| Outcome | *pending* | Graded when the site is live and demo links work on the user's phone. |

---

## Running averages

| Metric | Average |
|--------|---------|
| Code/Craft | **8.0** |
| Thinking | **8.25** |
| Outcome | — (first re-grades land when batch-1 replies come in) |

## What the grades buy

- Any round that ships a bug the user finds (not me): **capped at 5.**
- Any outreach advice that burns a lead (like an unverified "you have no site" send): **capped at 4.**
- First client closed: that round's Outcome auto-starts at **9.**
- $200 month hit: the month's rounds all get Outcome floors of **8.**
- Month 2 review: the boss can override any grade here with their own. Their number wins.
