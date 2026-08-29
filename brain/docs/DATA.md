# Market data infrastructure

The database is the source of truth for market data. Engines read it; only the
ingestion service talks to a vendor. That inversion is the point: it makes cost
proportional to *new data* rather than to curiosity, and it makes a backtest
reproducible because the bars it ran on can be pinned.

```
  MARKET DATA PROVIDERS      alpaca · tiingo · polygon · stooq · yahoo · csv · synthetic
            |
   INGESTION SERVICE         batched · gap-aware · incremental · records every call
            |
  VALIDATION / NORMALISE     rejects bad bars; flags suspicious ones
            |
        DATABASE             market.db -- raw bars, ticks, corporate actions, options
            |
   INDICATOR ENGINE          technical_indicators   (derived, droppable)
            |
    FEATURE ENGINE           strategy_features      (derived, droppable)
            |
        SCREENER             one indexed SQL statement, no recomputation
            |
     BACKTEST ENGINE         locks a dataset version before it starts
            |
        AI BRAIN             queries tools; never remembers a price
```

## Quick start

```bash
python3 -m tradingbrain.cli data bootstrap     # universe -> history -> features -> context
python3 -m tradingbrain.cli data status        # coverage, freshness, quality, API usage
python3 -m tradingbrain.cli data screen        # SQL screener over the whole universe
python3 -m tradingbrain.cli serve              # Market Data page is in the left nav
```

`bootstrap` is incremental. Running it again costs almost nothing: the backfill
asks only for trading days the store is missing.

## Providers

| Provider | Historical | Intraday | Realtime | Options | Corp. actions | Universe | Needs |
|---|---|---|---|---|---|---|---|
| `db` | ✓ | ✓ | | | | ✓ | nothing — this is the local store |
| `alpaca` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | `ALPACA_API_KEY` / `ALPACA_API_SECRET` |
| `tiingo` | ✓ | ✓ | | | | | `TIINGO_API_KEY` |
| `polygon` | ✓ | ✓ | | ✓ | | | `POLYGON_API_KEY` |
| `stooq` | ✓ | | | | | | network egress |
| `yahoo` | ✓ | ✓ | | ✓ | | | network egress |
| `csv` | ✓ | ✓ | | | | ✓ | files on disk |
| `synthetic` | ✓ | ✓ | | | | ✓ | nothing — always labelled SYNTHETIC |

Adding another (Massive, Databento, Norgate) means implementing the protocols in
`data/providers/base.py` and registering it. Nothing above the provider layer
changes: the DataHub, ingestion service, screener and backtester all address
providers through capabilities, never by name.

## Alpaca, and the feed you are actually getting

**A free Alpaca key streams IEX.** IEX is one exchange carrying a low
single-digit percentage of consolidated US volume. A relative-volume filter,
a liquidity screen or a breakout-volume rule computed from IEX prints is not
measuring the market — it is measuring a slice of it.

The system never hides this:

- `AlpacaProvider.feed_label()` returns `IEX` or `SIP`, and `feed_description()`
  spells out the coverage consequence.
- Every stored bar records `provider` **and** `feed`.
- The provider table shows a `PARTIAL` / `consolidated` badge.
- The data-status pill in the header shows the feed on every screen.

Set `ALPACA_DATA_FEED=sip` with a paid subscription to get the consolidated tape.
No code changes; the interface was designed for the swap.

Credentials are backend-only. `Settings.redacted()` replaces them with `<set>`,
no API route returns them, and `tests/test_alpaca.py` asserts a secret cannot
leak through `describe()`.

## The database

Two files, deliberately:

- **`brain.db`** — research artefacts: knowledge base, hypotheses, experiments,
  strategies, backtests, journal. Small.
- **`market.db`** — bars and features. Grows into the gigabytes.

### Raw versus derived

The split is structural, not a convention. `MarketStore` exposes **no method**
that can write a computed value into a `market_data_*` table.

| Raw (costs API calls to replace) | Derived (recomputable, droppable) |
|---|---|
| `market_data_daily` `_1m` `_5m` `_15m` `_1h` | `technical_indicators` |
| `raw_trades`, `raw_quotes` | `strategy_features` |
| `corporate_actions`, `options_contracts` | `market_regimes`, `sector_data` |
| `symbols` | `scanner_runs`, `scanner_results` |

"Clear derived tables" in the UI drops the right-hand column and leaves the left
untouched. Clearing raw data is refused from the UI on purpose.

### Never silently mixing vendors

A bar already present from a *different* provider is not overwritten. The write
is counted as a conflict, logged, and skipped unless the caller passes
`allow_provider_override=True`. A series assembled half from Alpaca IEX and half
from a CSV export is not a series of anything, and a backtest over the seam
measures the seam.

## Incremental everything

`missing_daily_ranges()` compares what the store holds against the **exchange
calendar** — a rules-based NYSE calendar covering weekends, the nine (ten since
2022) scheduled holidays, Good Friday, weekend-observance shifts and known
unscheduled closures. It is validated against published session counts: 250 in
2023, 252 in 2024, 250 in 2025.

Two consequences:

1. Holidays are not mistaken for gaps, so warnings stay meaningful.
2. Contiguous missing days merge into ranges, so a month-long hole is one
   request rather than twenty-two.

`incremental_sync` asks only for `[last stored bar + 1 trading day, today]`.
`tests/test_market_data.py` asserts that a second backfill over a covered range
makes **zero** vendor calls.

## Data quality

Every ingestion validates before writing. A **blocking** error (high below low,
non-positive price, NaN, duplicate timestamps) means the bars are not stored at
all and the sync status records why. Warnings (calendar gaps, suspected
unadjusted splits, zero-volume runs, out-of-session intraday stamps) are written
to `data_quality_flags` and shown in the control centre.

Suspicious data is flagged, never silently accepted.

## Real-time

`cli data stream` (or the Market Data page) starts a WebSocket ingestor that:

- authenticates, subscribes, and pumps messages on a background thread;
- reconnects with **full-jitter exponential backoff**, capped, forever;
- sends a heartbeat and forces a reconnect after three missed intervals;
- drops **duplicate** bars (streams replay on reconnect) and counts them;
- detects **gaps** inside the regular session and flags them, because the
  aggregate spanning a gap understates volume;
- **batches** writes on an interval instead of one transaction per message;
- rolls 1m into 5m/15m/1h and refreshes the in-progress daily bar.

The WebSocket client is hand-written over `socket`/`ssl` (no dependency) and is
tested against a real RFC 6455 server in `tests/test_websocket.py`: handshake,
masking, fragmentation, 64KB payloads, ping/pong, close codes, abrupt
disconnects.

## Dataset versioning

Before a backtest starts, the engine locks a dataset version: a checksum over
every `(symbol, ts, o, h, l, c, v)` in scope, plus provider, feed, adjustment
methodology, universe, date range and row count. The result stores the id.

Re-adjusting, backfilling or switching provider produces a **different**
checksum, so an old result stays attributable to the data it actually saw. Each
backtest also stores strategy version and fingerprint, slippage and commission
assumptions, and the lock timestamp.

## The screener

One indexed SQL statement over `strategy_features`, joined to
`technical_indicators` and `symbols`. On the shipped 96-symbol universe it runs
in ~0.1s and makes **zero** API calls; `tests/test_integration.py` asserts the
API-call counter does not move.

Filters the system cannot honour are **refused explicitly**, never dropped:

| Filter | What it needs |
|---|---|
| `min_market_cap` | shares outstanding from a fundamentals provider |
| `max_days_to_earnings` | an earnings calendar (Finnhub; Alpaca has none) |
| `min_option_open_interest` | an options chain in the store for the scan date |
| `float_shares`, `short_interest` | not derivable from price data |

## Getting real data in

### 1. Alpaca (recommended)

```bash
export ALPACA_API_KEY=...  ALPACA_API_SECRET=...  ALPACA_DATA_FEED=iex
python3 -m tradingbrain.cli data bootstrap --years 5 --max-symbols 500
```

### 2. CSV drop (works offline)

Put files at `data_cache/daily/<SYMBOL>.csv` with a date column plus
open/high/low/close/volume. Use split- and dividend-adjusted prices; the
validator flags suspected unadjusted actions but cannot repair them. Include
`SPY` and `QQQ` — the regime filter needs them. Then:

```bash
python3 -m tradingbrain.cli data backfill --provider csv
python3 -m tradingbrain.cli data features
```

### 3. Tiingo / Polygon

Set the key, put the provider first in `BRAIN_INGEST_PROVIDER_ORDER`, bootstrap.

## Cost control

- Local database first; a read never becomes a request.
- Batched requests: 100 symbols per bars call.
- Incremental sync; covered ranges are never re-requested.
- WebSocket for live data instead of polling.
- Precomputed features, so a screen is a query rather than a computation.
- Every call recorded in `api_usage` with endpoint, rows, latency and errors —
  visible in the control centre before the bill arrives.

## The bias you cannot fix by adding data

A universe of today's listed symbols excludes everything delisted, acquired or
bankrupt. The `symbols` table carries `active`, `ipo_date` and `delisted_date` so
a point-in-time constituent file can be imported, but until one is, every
backtest result carries the survivorship warning and should be read as an upper
bound.
