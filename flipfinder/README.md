# FlipFinder — eBay deal scanner

Finds listings priced well below market, then does the full margin math
(resale − eBay fees − shipping − buy price) so every flip is a numbers
decision instead of a gut feeling.

## Try it right now (no setup)

```bash
python3 flipfinder.py --mock "nintendo switch lite"
```

Mock mode generates a realistic fake market so you can learn how the tool
thinks before touching real money.

## Go live (free eBay developer keys, ~10 min)

1. Sign up at https://developer.ebay.com (free, instant for the Browse API)
2. Create an app → copy the **Client ID** and **Client Secret** (Production keys)
3. Set them:
   ```bash
   export EBAY_CLIENT_ID="your-client-id"
   export EBAY_CLIENT_SECRET="your-client-secret"
   ```
4. Scan for real:
   ```bash
   python3 flipfinder.py "dewalt 20v drill"
   ```

**Never commit the keys to this repo.** Environment variables only.

## Useful flags

| Flag | Default | What it does |
|------|---------|--------------|
| `--max-buy` | 120 | Skip anything costing more (month-1 discipline rule) |
| `--min-profit` | 25 | Skip anything projected under this |
| `--deal-threshold` | 0.72 | Only flag listings ≤ 72% of market median |
| `--limit` | 50 | Listings pulled per scan |

## How to actually use it (the workflow)

1. Pick 2–3 product categories **you can judge from photos** — that's your edge,
   the tool can't see a cracked screen.
2. Scan each one daily. Takes 2 minutes. Deals appear and vanish fast.
3. When something's flagged: open the listing, check photos/seller rating/what's
   included. Most flagged items you'll pass on — "cheap for a reason" is common.
4. Buy only when the price makes sense AND the projected profit ≥ $25.
5. Relist with good photos at slightly under median for a fast sale.
6. Log the buy and the sale in `../tracker/ledger.csv`.

## Honest limitations

- Resale is estimated from the **median of active listings**, not sold prices —
  eBay locks true sold-price data behind a restricted API. Active-listing median
  runs a bit high, which is why the default rules are conservative.
- It can't judge condition, spot scams, or price rare variants. You're the
  domain expert; it's the metal detector.
