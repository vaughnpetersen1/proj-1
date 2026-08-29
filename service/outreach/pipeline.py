#!/usr/bin/env python3
"""Outreach pipeline status. Run: python3 service/outreach/pipeline.py

Reads leads.csv and tells you exactly what to do today:
which leads are due a follow-up, which are untouched, and how the funnel looks.

Statuses: lead -> contacted -> followup1 -> followup2 -> replied -> closed_won / closed_lost
(use "dead" for wrong numbers, closed businesses, etc.)
"""

import csv
import os
from datetime import date, datetime

LEADS = os.path.join(os.path.dirname(__file__), "leads.csv")

FOLLOWUP_AFTER_DAYS = {"contacted": 3, "followup1": 7}

# House rule 10 (boss-set 2026-07-30): these niches are too price-sensitive and
# have said no repeatedly. They never enter the send queue -- see ICP.md.
OFF_ICP_NICHES = {"barber", "salon", "nail", "beauty", "barbershop"}
FUNNEL_ORDER = ["lead", "contacted", "followup1", "followup2", "replied",
                "closed_won", "closed_lost", "dead"]


def days_since(datestr: str) -> int:
    return (date.today() - datetime.strptime(datestr, "%Y-%m-%d").date()).days


def main() -> None:
    with open(LEADS, newline="") as f:
        rows = [r for r in csv.DictReader(f) if not r["business"].startswith("EXAMPLE")]

    if not rows:
        print("\nNo leads yet. Google Maps + 15 minutes = 6 leads. Go get 'em.\n")
        return

    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    print(f"\n📊 PIPELINE — {len(rows)} leads")
    print("   " + "  ·  ".join(f"{s}: {counts[s]}" for s in FUNNEL_ORDER if s in counts))

    all_untouched = [r for r in rows if r["status"] == "lead"]
    untouched = [r for r in all_untouched
                 if r["niche"].strip().lower() not in OFF_ICP_NICHES]
    off_icp = [r for r in all_untouched
               if r["niche"].strip().lower() in OFF_ICP_NICHES]
    due = [r for r in rows
           if r["status"] in FOLLOWUP_AFTER_DAYS
           and days_since(r["last_touch"]) >= FOLLOWUP_AFTER_DAYS[r["status"]]]
    hot = [r for r in rows if r["status"] == "replied"]

    if hot:
        print(f"\n🔥 REPLIED — answer these FIRST ({len(hot)}):")
        for r in hot:
            print(f"   • {r['business']} ({r['niche']}) via {r['channel']}: {r['contact']} — {r['notes']}")

    if due:
        print(f"\n⏰ FOLLOW-UPS DUE ({len(due)}):")
        for r in due:
            nxt = "followup1" if r["status"] == "contacted" else "followup2 (final)"
            print(f"   • {r['business']} ({r['niche']}) — {days_since(r['last_touch'])}d since last touch → send {nxt}")

    if untouched:
        print(f"\n📬 NEVER CONTACTED — SENDABLE ({len(untouched)}):")
        for r in untouched[:8]:
            print(f"   • {r['business']} ({r['niche']}) via {r['channel']}: {r['contact']}")
        if len(untouched) > 8:
            print(f"   ...and {len(untouched) - 8} more")

    if off_icp:
        print(f"\n🚫 OFF-ICP — DO NOT SEND ({len(off_icp)}), house rule 10:")
        for r in off_icp:
            print(f"   • {r['business']} ({r['niche']}, {r['city']})")
        print("   Price-sensitive niches. Mark 'disqualified' or requalify per ICP.md.")

    won = counts.get("closed_won", 0)
    finished = won + counts.get("closed_lost", 0) + counts.get("dead", 0)
    if finished:
        print(f"\n🏁 Close rate so far: {won}/{finished} of resolved leads")
    if not (hot or due or untouched):
        print("\n✅ Nothing due today. Add new leads or enjoy the day off.")
    print()


if __name__ == "__main__":
    main()
