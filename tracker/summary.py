#!/usr/bin/env python3
"""Monthly P&L from ledger.csv. Run: python3 tracker/summary.py"""

import csv
import os
from collections import defaultdict

LEDGER = os.path.join(os.path.dirname(__file__), "ledger.csv")

months = defaultdict(lambda: defaultdict(float))
with open(LEDGER, newline="") as f:
    for row in csv.DictReader(f):
        month = row["date"][:7]
        months[month][row["engine"]] += float(row["amount"])
        months[month]["_net"] += float(row["amount"])

print(f"\n{'Month':<10}{'Service':>10}{'Flips':>10}{'Other':>10}{'NET':>10}   vs $200 goal")
print("-" * 62)
for month in sorted(months):
    m = months[month]
    other = m["_net"] - m.get("service", 0) - m.get("flips", 0)
    net = m["_net"]
    goal = "✅ HIT" if net >= 200 else f"${200 - net:.0f} to go"
    print(f"{month:<10}{m.get('service', 0):>10.2f}{m.get('flips', 0):>10.2f}{other:>10.2f}{net:>10.2f}   {goal}")
print()
