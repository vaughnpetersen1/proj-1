#!/usr/bin/env python3
"""FlipFinder — find underpriced eBay listings and do the margin math.

Usage:
    python3 flipfinder.py --mock "nintendo switch lite"      # no keys needed
    python3 flipfinder.py "nintendo switch lite"             # live eBay data
    python3 flipfinder.py "dewalt drill" --max-buy 120 --min-profit 25

Live mode needs free eBay developer keys (see README.md):
    export EBAY_CLIENT_ID=...
    export EBAY_CLIENT_SECRET=...

No third-party dependencies — stdlib only.
"""

import argparse
import base64
import json
import os
import random
import statistics
import sys
import urllib.parse
import urllib.request

EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

# eBay final value fee for most categories (2026): 13.25% up to $7,500, + $0.30/order
FEE_RATE = 0.1325
FEE_FIXED = 0.30


def get_token(client_id: str, client_secret: str) -> str:
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope",
    }).encode()
    req = urllib.request.Request(EBAY_TOKEN_URL, data=body, headers={
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["access_token"]


def search_live(query: str, limit: int) -> list[dict]:
    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit(
            "Missing EBAY_CLIENT_ID / EBAY_CLIENT_SECRET.\n"
            "Get free keys at https://developer.ebay.com (see README.md), "
            "or run with --mock to try the tool without keys."
        )
    token = get_token(client_id, client_secret)
    params = urllib.parse.urlencode({
        "q": query,
        "limit": str(limit),
        "filter": "buyingOptions:{FIXED_PRICE},conditions:{USED|VERY_GOOD|GOOD|ACCEPTABLE|NEW}",
    })
    req = urllib.request.Request(f"{EBAY_SEARCH_URL}?{params}", headers={
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)

    listings = []
    for item in data.get("itemSummaries", []):
        price = float(item.get("price", {}).get("value", 0))
        if price <= 0:
            continue
        ship = 0.0
        for opt in item.get("shippingOptions", []):
            cost = opt.get("shippingCost", {}).get("value")
            if cost is not None:
                ship = float(cost)
                break
        listings.append({
            "title": item.get("title", "?"),
            "price": price,
            "shipping": ship,
            "condition": item.get("condition", "?"),
            "url": item.get("itemWebUrl", ""),
        })
    return listings


def search_mock(query: str, limit: int) -> list[dict]:
    """Plausible fake market data so the tool is testable with zero setup."""
    random.seed(query)  # same query -> same fake market, so runs are comparable
    base = random.uniform(40, 260)
    conditions = ["Used", "Very Good", "Good", "Acceptable", "New (Other)"]
    listings = []
    for i in range(limit):
        # most listings cluster near market price; a few are mispriced gems/lemons
        spread = random.gauss(1.0, 0.18)
        if random.random() < 0.08:
            spread = random.uniform(0.45, 0.70)  # the underpriced ones we hunt
        price = round(max(5, base * spread), 2)
        listings.append({
            "title": f"{query.title()} — lot #{i + 1} ({random.choice(['tested', 'works great', 'as-is', 'bundle', 'no box'])})",
            "price": price,
            "shipping": round(random.choice([0, 0, 5.99, 8.99, 12.50]), 2),
            "condition": random.choice(conditions),
            "url": f"https://www.ebay.com/itm/mock{i + 1}",
        })
    return listings


def analyze(listings: list[dict], max_buy: float, min_profit: float,
            deal_threshold: float) -> tuple[float, list[dict]]:
    """Return (median_market_price, deals sorted by projected profit)."""
    totals = [l["price"] + l["shipping"] for l in listings]
    median = statistics.median(totals)

    deals = []
    for l in listings:
        buy_cost = l["price"] + l["shipping"]
        if buy_cost > max_buy or buy_cost > median * deal_threshold:
            continue
        # resell at median, assume ~$10 to ship it back out
        resale = median
        fees = resale * FEE_RATE + FEE_FIXED
        profit = resale - fees - 10.0 - buy_cost
        if profit < min_profit:
            continue
        deals.append({**l, "buy_cost": buy_cost, "est_resale": resale,
                      "est_fees": fees, "est_profit": profit,
                      "roi": profit / buy_cost * 100})
    deals.sort(key=lambda d: d["est_profit"], reverse=True)
    return median, deals


def main() -> None:
    p = argparse.ArgumentParser(description="Find underpriced eBay listings.")
    p.add_argument("query", help='what to search, e.g. "nintendo switch lite"')
    p.add_argument("--mock", action="store_true", help="use fake data (no API keys needed)")
    p.add_argument("--limit", type=int, default=50, help="listings to pull (default 50)")
    p.add_argument("--max-buy", type=float, default=120, help="max total buy cost, month-1 rule is $120 (default)")
    p.add_argument("--min-profit", type=float, default=25, help="skip anything projected under this (default $25)")
    p.add_argument("--deal-threshold", type=float, default=0.72,
                   help="flag listings priced below this fraction of market median (default 0.72)")
    args = p.parse_args()

    listings = search_mock(args.query, args.limit) if args.mock else search_live(args.query, args.limit)
    if len(listings) < 8:
        sys.exit(f"Only {len(listings)} listings found — not enough for reliable market stats. Broaden the query.")

    median, deals = analyze(listings, args.max_buy, args.min_profit, args.deal_threshold)

    mode = "MOCK DATA" if args.mock else "LIVE eBay"
    print(f"\n🔎 {args.query}  [{mode}]")
    print(f"   {len(listings)} listings · market median (item+ship): ${median:.2f}")
    print(f"   rules: buy ≤ ${args.max_buy:.0f} · profit ≥ ${args.min_profit:.0f} · price ≤ {args.deal_threshold:.0%} of median\n")

    if not deals:
        print("   No deals right now — that's normal, most scans find nothing.")
        print("   Re-run daily; mispriced listings appear and vanish within hours.\n")
        return

    for i, d in enumerate(deals[:10], 1):
        print(f"   #{i}  ${d['buy_cost']:>7.2f} buy → est. profit ${d['est_profit']:.2f}  ({d['roi']:.0f}% ROI)")
        print(f"       {d['title'][:78]}")
        print(f"       {d['condition']} · resale ~${d['est_resale']:.2f} − fees ${d['est_fees']:.2f} − ~$10 ship")
        print(f"       {d['url']}\n")

    print("   ⚠️  The math assumes resale at market median. YOU judge condition from")
    print("       the photos before buying — the tool finds candidates, not sure things.\n")


if __name__ == "__main__":
    main()
