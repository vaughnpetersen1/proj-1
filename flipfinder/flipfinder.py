#!/usr/bin/env python3
"""FlipFinder — find underpriced eBay listings and do the margin math.

Usage:
    python3 flipfinder.py --mock "nintendo switch lite"      # no keys needed
    python3 flipfinder.py "nintendo switch lite"             # live eBay data
    python3 flipfinder.py --watchlist                        # scan every saved search
    python3 flipfinder.py "dewalt drill" --max-buy 120 --min-profit 25

Live mode needs free eBay developer keys (see README.md):
    export EBAY_CLIENT_ID=...
    export EBAY_CLIENT_SECRET=...

Live-mode deals are appended to deals_log.csv so you can review what the
market offered over time. No third-party dependencies — stdlib only.
"""

import argparse
import base64
import csv
import json
import os
import random
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_PATH = os.path.join(HERE, "watchlist.json")
DEALS_LOG_PATH = os.path.join(HERE, "deals_log.csv")

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


def search_live(query: str, limit: int, token: str) -> list[dict]:
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


def log_deals(query: str, deals: list[dict]) -> None:
    """Append live-mode deals to deals_log.csv for later review."""
    new_file = not os.path.exists(DEALS_LOG_PATH)
    with open(DEALS_LOG_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "query", "title", "condition", "buy_cost",
                        "est_resale", "est_profit", "roi_pct", "url"])
        for d in deals:
            w.writerow([date.today().isoformat(), query, d["title"], d["condition"],
                        f"{d['buy_cost']:.2f}", f"{d['est_resale']:.2f}",
                        f"{d['est_profit']:.2f}", f"{d['roi']:.0f}", d["url"]])


def run_scan(query: str, args: argparse.Namespace, token: str | None,
             max_buy: float | None = None, min_profit: float | None = None) -> int:
    """Scan one query, print results, log live deals. Returns deal count."""
    max_buy = max_buy if max_buy is not None else args.max_buy
    min_profit = min_profit if min_profit is not None else args.min_profit

    listings = search_mock(query, args.limit) if args.mock else search_live(query, args.limit, token)
    mode = "MOCK DATA" if args.mock else "LIVE eBay"

    print(f"\n🔎 {query}  [{mode}]")
    if len(listings) < 8:
        print(f"   Only {len(listings)} listings — not enough for reliable stats. Broaden the query.")
        return 0

    median, deals = analyze(listings, max_buy, min_profit, args.deal_threshold)
    print(f"   {len(listings)} listings · market median (item+ship): ${median:.2f}")
    print(f"   rules: buy ≤ ${max_buy:.0f} · profit ≥ ${min_profit:.0f} · price ≤ {args.deal_threshold:.0%} of median")

    if not deals:
        print("   No deals right now — normal. Mispriced listings appear and vanish within hours.")
        return 0

    print()
    for i, d in enumerate(deals[:10], 1):
        print(f"   #{i}  ${d['buy_cost']:>7.2f} buy → est. profit ${d['est_profit']:.2f}  ({d['roi']:.0f}% ROI)")
        print(f"       {d['title'][:78]}")
        print(f"       {d['condition']} · resale ~${d['est_resale']:.2f} − fees ${d['est_fees']:.2f} − ~$10 ship")
        print(f"       {d['url']}\n")

    if not args.mock:
        log_deals(query, deals)
    return len(deals)


def load_watchlist() -> list[dict]:
    if not os.path.exists(WATCHLIST_PATH):
        sys.exit(f"No watchlist yet. Create {WATCHLIST_PATH} — see watchlist.example.json")
    with open(WATCHLIST_PATH) as f:
        return json.load(f)


def main() -> None:
    p = argparse.ArgumentParser(description="Find underpriced eBay listings.")
    p.add_argument("query", nargs="?", help='what to search, e.g. "nintendo switch lite"')
    p.add_argument("--watchlist", action="store_true", help="scan every saved search in watchlist.json")
    p.add_argument("--mock", action="store_true", help="use fake data (no API keys needed)")
    p.add_argument("--limit", type=int, default=50, help="listings to pull per query (default 50)")
    p.add_argument("--max-buy", type=float, default=120, help="max total buy cost, month-1 rule is $120 (default)")
    p.add_argument("--min-profit", type=float, default=25, help="skip anything projected under this (default $25)")
    p.add_argument("--deal-threshold", type=float, default=0.72,
                   help="flag listings priced below this fraction of market median (default 0.72)")
    args = p.parse_args()

    if not args.query and not args.watchlist:
        p.error("give me a query, or use --watchlist to scan your saved searches")

    token = None
    if not args.mock:
        client_id = os.environ.get("EBAY_CLIENT_ID")
        client_secret = os.environ.get("EBAY_CLIENT_SECRET")
        if not client_id or not client_secret:
            sys.exit(
                "Missing EBAY_CLIENT_ID / EBAY_CLIENT_SECRET.\n"
                "Get free keys at https://developer.ebay.com (see README.md), "
                "or run with --mock to try the tool without keys."
            )
        token = get_token(client_id, client_secret)

    if args.watchlist:
        entries = load_watchlist()
        total = 0
        for entry in entries:
            total += run_scan(entry["query"], args, token,
                              max_buy=entry.get("max_buy"),
                              min_profit=entry.get("min_profit"))
        print(f"\n{'=' * 50}")
        print(f"   Watchlist done: {len(entries)} searches, {total} deals flagged."
              + (f" Logged to deals_log.csv." if total and not args.mock else ""))
    else:
        run_scan(args.query, args, token)

    print("\n   ⚠️  The math assumes resale at market median. YOU judge condition from")
    print("       the photos before buying — the tool finds candidates, not sure things.\n")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # output was piped to head/less and closed early — not an error
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
