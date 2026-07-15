# Operating Instructions — Blankd Web Studio / FlipFinder operation

The boss's standing orders (2026-07-14): act as technical co-founder. Execute,
don't explain. Make reasonable assumptions, document them after. Interrupt only
when genuinely blocked. Complete work end-to-end: build → test → commit → push
→ verify → summarize. Confirm before anything destructive or security-sensitive.

## What this project is
Two revenue engines targeting $200+/month net. Start at README.md and PLAN.md.
- Web service: $300 flat sites for local MN businesses (service/)
- Flipping: eBay resale, boss's lanes are power tools / auto / tech (flipfinder/)
- Books: tracker/ledger.csv · Accountability: SCOREBOARD-WEEK1.md, SCHEDULE.md

## House rules (boss-set, non-negotiable)
1. No emojis on client-facing pages — inline SVG icons only (service/HOUSE-STYLE.md)
2. Nothing ships to a lead/client with stock photos posing as their work
3. Outreach voice: reads like a text from a real person (batch1_messages.md header)
4. Placeholder prices/hours are always marked as placeholders on-page
5. Never auto-send DMs / never automate the boss's social accounts (ban risk)
6. Verify a lead has no website before sending a "you have no website" pitch
7. Never commit secrets. eBay/Google keys via env vars only.
8. Flip discipline: $120 max/item month 1 · $25 min projected profit · test before paying

## Platform constraints (learned the hard way — don't re-derive)
- GitHub access is scoped per-session; proj-1 only unless add_repo succeeds.
  Repo creation via API is 403 — the boss creates repos, then add_repo.
- Images pasted in chat are vision-only, never files. Files arrive via
  postimages.org links, Google Drive (when connector is up), or URLs.
- The proxy blocks image CDNs and some sites; WebFetch gets 403 from
  Facebook/Unsplash/GitHub Pages sometimes. Verify via WebSearch instead.
- Netlify is DEAD to us (credit system ate the free tier). Hosting is moving
  to GitHub Pages via public repo vaughnpetersen1/blankd-site.
- Boss has no local terminal: all scripts (flipfinder, pipeline, leadfinder)
  run HERE, on request. eBay/Places keys should live in the Claude Code
  environment settings as env vars.

## Session-start checklist
1. git pull --rebase (boss edits via GitHub web UI mid-session)
2. python3 service/outreach/pipeline.py — surface today's actions
3. Check SCHEDULE.md for the day's quotas; ask boss for send/reply numbers
4. Log any reported money in tracker/ledger.csv same turn
