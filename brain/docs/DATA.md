# Getting real market data into the system

The default data path is a **synthetic generator**. It is clearly labelled
everywhere and produces nothing that should inform a real trade. Here is how to
replace it, easiest first.

## 1. CSV drop — works with no network and no API key

Export daily bars from your broker, TC2000, Norgate, or anywhere else, and put
them here:

```
brain/data_cache/daily/NVDA.csv
brain/data_cache/daily/SPY.csv
brain/data_cache/daily/QQQ.csv
...
```

Header must contain a date column (`date`, `timestamp`, `time`, `datetime`, or
`t`) plus open/high/low/close/volume in any order and any common casing.
Adjusted-close columns (`adj close`, `adj_close`) are recognised.

```csv
date,open,high,low,close,volume
2024-01-02,49.52,49.98,48.73,49.10,412839200
2024-01-03,48.79,49.10,47.58,47.58,320896000
```

Intraday goes in `data_cache/intraday/<timeframe>/<SYMBOL>.csv` where timeframe
is one of `1m`, `5m`, `15m`, `1h`.

**Use split- and dividend-adjusted prices.** The quality validator flags
suspected unadjusted corporate actions, but it cannot fix them, and long-horizon
statistics computed on unadjusted data are fiction.

Include `SPY` and `QQQ` — the market-regime filter needs them.

Verify: `python3 -m tradingbrain.cli providers` should show `csv: available`.

## 2. An API key

| Variable | Unlocks |
|---|---|
| `TIINGO_API_KEY` | daily + intraday equities history (free tier available) |
| `POLYGON_API_KEY` | daily + intraday + **the real options chain** (bid/ask, OI, IV) |
| `ALPACA_KEY_ID` / `ALPACA_SECRET_KEY` | alternative data, future paper broker |
| `FINNHUB_API_KEY` | fundamentals / earnings calendar (not yet consumed) |
| `NEWS_API_KEY` | news, which is what episodic-pivot detection is blocked on |

Set them in the environment (or a `.env` you load), then restart. The provider
order is `BRAIN_PROVIDER_ORDER`, default
`csv,stooq,yahoo,tiingo,polygon,synthetic`.

## 3. Free, no key — if the network allows it

`stooq` (daily EOD) and `yahoo` (daily + intraday + options) are implemented and
need no credentials. They failed in the build environment only because the egress
proxy denies CONNECT to those hosts. On an ordinary machine they work as-is.

## What each engine needs

| Engine | Minimum |
|---|---|
| Regime | SPY (and ideally QQQ), 250+ bars |
| Sector / theme ranking | 5+ symbols per group, 130+ bars |
| Scanner, backtester | any universe, 60+ bars per symbol |
| Historical analogs | the more symbols and years, the narrower the intervals |
| Opening-range entry | intraday bars — otherwise it stays untested |
| Options Lab (real quotes) | `POLYGON_API_KEY`, or reachable Yahoo |
| Episodic pivots | a **point-in-time** news/earnings feed. Nothing else will do. |

## The bias you cannot fix by adding data

A universe of today's listed symbols excludes everything that was delisted,
acquired or went to zero. Every backtest result carries this warning. The only
real fix is a point-in-time constituent file (Norgate, CRSP, Sharadar). Until
then, read every backtest as an upper bound.
