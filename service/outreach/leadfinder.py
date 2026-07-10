#!/usr/bin/env python3
"""LeadFinder — find local businesses with NO website via the Google Places API.

This automates the manual hunt: it searches a niche + city on Google Maps
and returns only businesses that have no website listed on their profile —
the exact signal customers see when they google.

Usage:
    python3 leadfinder.py --mock "barbershop in St Cloud MN"   # no key needed
    python3 leadfinder.py "handyman in Duluth MN"              # live data
    python3 leadfinder.py "auto detailing in Rochester MN" --min-reviews 3

Live mode needs a free Google Places API key (~10 min setup, see README):
    export GOOGLE_PLACES_API_KEY=...

Google gives every account substantial free monthly usage — at our volume
(a few searches a day) this costs $0. Results append to found_leads.csv
for verification before anyone gets messaged (house rule: 30-sec google
check still applies — Places data can lag reality).

Stdlib only, no dependencies.
"""

import argparse
import csv
import json
import os
import random
import sys
import urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
FOUND_CSV = os.path.join(HERE, "found_leads.csv")

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELDS = ("places.displayName,places.formattedAddress,places.nationalPhoneNumber,"
          "places.websiteUri,places.rating,places.userRatingCount,places.businessStatus")


def search_live(query: str, api_key: str) -> list[dict]:
    body = json.dumps({"textQuery": query, "pageSize": 20}).encode()
    req = urllib.request.Request(PLACES_URL, data=body, headers={
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELDS,
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    out = []
    for p in data.get("places", []):
        out.append({
            "name": p.get("displayName", {}).get("text", "?"),
            "address": p.get("formattedAddress", ""),
            "phone": p.get("nationalPhoneNumber", ""),
            "website": p.get("websiteUri", ""),
            "rating": p.get("rating", ""),
            "reviews": p.get("userRatingCount", 0),
            "status": p.get("businessStatus", ""),
        })
    return out


def search_mock(query: str) -> list[dict]:
    """Fake results so the tool is testable before the API key exists."""
    random.seed(query)
    niche = query.split(" in ")[0].title()
    out = []
    for i in range(random.randint(8, 14)):
        has_site = random.random() < 0.6
        out.append({
            "name": f"{random.choice(['North','Lake','Granite','Pine','Twin','Iron'])} {niche} #{i+1}",
            "address": f"{random.randint(100,9999)} Main St, Mocktown, MN",
            "phone": f"(218) 555-{random.randint(1000,9999)}",
            "website": "https://example.com" if has_site else "",
            "rating": round(random.uniform(3.8, 5.0), 1),
            "reviews": random.randint(0, 120),
            "status": "OPERATIONAL",
        })
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Find businesses with no website listed.")
    p.add_argument("query", help='e.g. "barbershop in St Cloud MN"')
    p.add_argument("--mock", action="store_true", help="fake data, no API key needed")
    p.add_argument("--min-reviews", type=int, default=0,
                   help="skip businesses with fewer reviews (0 = keep all; ghosts have no reviews)")
    args = p.parse_args()

    if args.mock:
        places = search_mock(args.query)
    else:
        key = os.environ.get("GOOGLE_PLACES_API_KEY")
        if not key:
            sys.exit("Missing GOOGLE_PLACES_API_KEY. See README for the 10-minute setup, "
                     "or run with --mock to try the tool.")
        places = search_live(args.query, key)

    mode = "MOCK" if args.mock else "LIVE"
    leads = [x for x in places
             if not x["website"]
             and x["status"] in ("OPERATIONAL", "")
             and x["reviews"] >= args.min_reviews]
    with_sites = len([x for x in places if x["website"]])

    print(f"\nLeadFinder [{mode}] — {args.query}")
    print(f"   {len(places)} businesses found · {with_sites} have websites · {len(leads)} NO-WEBSITE LEADS\n")

    if not leads:
        print("   Nothing here — try the next town or niche.\n")
        return

    new_file = not os.path.exists(FOUND_CSV)
    with open(FOUND_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date_found", "query", "business", "address", "phone",
                        "rating", "reviews", "verified"])
        for l in leads:
            print(f"   • {l['name']}  ({l['rating']}★ / {l['reviews']} reviews)")
            print(f"     {l['address']}  {l['phone']}\n")
            w.writerow([date.today().isoformat(), args.query, l["name"], l["address"],
                        l["phone"], l["rating"], l["reviews"], "NO - verify before sending"])

    print(f"   Saved to found_leads.csv. HOUSE RULE: 30-second google check on each")
    print(f"   before messaging — no-site-on-Maps is a strong signal, not proof.\n")


if __name__ == "__main__":
    main()
