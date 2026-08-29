# AI Trading Brain

A quantitative research and decision-support system built around the SAR /
Morgan Trades momentum breakout methodology, extended with publicly-stated
concepts from Kristjan Kullamägi's material.

It is **not** a signal service and not a profit machine. It is an apparatus for
turning trading beliefs into measurable rules, testing those rules against
historical data, and reporting the result honestly — including when the result
contradicts the source that suggested the rule.

```
python3 -m pip install -r requirements.txt
python3 -m tradingbrain.cli seed              # knowledge base, concepts, hypotheses
python3 -m tradingbrain.cli data bootstrap    # universe -> history -> features -> context
python3 -m tradingbrain.cli serve             # http://127.0.0.1:8787
```

---

## The one thing to understand first

**Every number this system produces carries an evidence class, and no number is
allowed to change class silently.**

| Class | Means |
|---|---|
| `SOURCE_FACT` | A named source said this. Recording it does not make it true. |
| `INFERENCE` | A reasoning step this system took. Check the premises. |
| `HYPOTHESIS` | Untested. No evidential weight until an experiment runs. |
| `BACKTEST_RESULT` | Simulated on historical data under stated assumptions. |
| `LIVE_MARKET_OBSERVATION` | A measurement at a stated timestamp from a stated provider. |
| `MODEL_ESTIMATE` | Output of a model (Black-Scholes, Monte Carlo). Never a market price. |

Proportions are phrased as *"63.4% of the 428 historical qualifying events were
positive after 10 trading days"* — never *"63.4% chance"*. Every statistic shows
its sample size, period, definition of success, data source and method. This is
enforced in `tradingbrain/provenance.py`, not left to discipline.

## The second thing: the database is the source of truth

**Nothing downloads market data to answer a question.** The system maintains its
own continuously-updated store; engines read it, and only the ingestion service
talks to a vendor.

```
PROVIDERS -> INGESTION -> VALIDATION -> DATABASE -> INDICATORS -> FEATURES
                                            |
                              SCREENER · BACKTESTER · AI BRAIN
```

- **Incremental by construction.** Missing ranges are computed against a real
  NYSE calendar (validated against published session counts: 250/252/250 for
  2023/24/25), merged into contiguous blocks, and batched 100 symbols per
  request. A second backfill over a covered range makes **zero** vendor calls —
  asserted in the tests.
- **Precomputed features.** The screener runs one indexed SQL statement over
  `strategy_features` instead of recomputing a 200-bar SMA per symbol per
  request: ~0.1s for the whole universe, no API calls.
- **Dataset versioning.** A backtest checksums every bar in scope before it
  starts and stores the id, so a result stays reproducible after the store grows,
  gets re-adjusted, or changes provider.
- **Vendors never mix silently.** A bar already present from a different provider
  is not overwritten; the conflict is counted and logged.
- **Raw and derived are structurally separate.** No method in `MarketStore` can
  write a computed value into a `market_data_*` table. Derived tables are
  droppable and carry a feature version plus a hash of the parameters that made
  them.

`python3 -m tradingbrain.cli data status` (or the **Market Data** page) shows
coverage, freshness, provider health, quality flags, API usage and locked
datasets.

### Alpaca, and the feed you are actually getting

Alpaca is the first-class provider: historical bars, intraday, trades, quotes,
corporate actions, options and a real-time WebSocket stream, behind
`ALPACA_API_KEY` / `ALPACA_API_SECRET` / `ALPACA_DATA_FEED`. Credentials are
backend-only and never reach the frontend.

**A free Alpaca key streams IEX** — one exchange carrying a low single-digit
share of consolidated US volume. Relative volume, liquidity and breakout-volume
filters computed from it understate the real market. The system says so
everywhere: the feed is stored on every bar, badged `PARTIAL` in the provider
table, and shown in the header on every screen. `ALPACA_DATA_FEED=sip` switches
to the consolidated tape with no code change.

### If you have no keys

The default path is a clearly-labelled **synthetic generator**, ingested through
the same pipeline into the same tables. Everything computed on it is stamped
`SYNTHETIC` and every screen says so. It exercises the machinery; it is not
evidence about markets. CSV drop, Tiingo, Polygon, Stooq and Yahoo are all
implemented alternatives — see [docs/DATA.md](docs/DATA.md).

---

## What is actually built

### Data layer
- Ten provider protocols — `MarketData`, `Historical`, `Intraday`,
  `RealtimeMarketData`, `Options`, `Fundamental`, `News`, `CorporateActions`,
  `SymbolUniverse`, `Broker` — so nothing above depends on a vendor.
- Adapters: **Alpaca** (bars, intraday, trades, quotes, corporate actions,
  options, WebSocket stream, asset universe), Tiingo, Polygon, Stooq, Yahoo, CSV
  cache, and the synthetic generator.
- **Persistent store** (`market.db`): per-timeframe bar tables, raw ticks and
  quotes, corporate actions, options contracts, sync status, quality flags, API
  usage, ingestion log, dataset versions.
- **Ingestion service**: gap-aware incremental backfill, batched requests,
  provider fallback with recorded reasons, corporate-action and options sync.
- **Real-time ingestion**: hand-written RFC 6455 WebSocket client (no
  dependency, tested against a live socket server), full-jitter reconnect,
  heartbeat, duplicate detection, gap detection, batched writes, and 1m→5m/15m/1h
  derivation.
- **Data-quality validation on ingest**: blocking errors are refused rather than
  stored; warnings are flagged and surfaced. Duplicate timestamps, OHLC
  inconsistency, calendar gaps against a real exchange calendar, non-positive
  prices, suspected unadjusted splits, zero-volume runs, out-of-session bars,
  plus a standing survivorship-bias disclosure.
- A cross-sectional `Panel` so relative strength and breadth are computed against
  peers *on the same date*, never against a survivor list drawn from later.

### Rule formalisation
Trading language is qualitative. `research/formalizer.py` holds each fuzzy
concept, its **preserved ambiguity**, and *every* competing quantitative
definition as a runnable strategy override:

> **"strong prior move"** — sources say "30%+ over days to weeks" and "30–100%+
> in 1–3 months". These differ in both magnitude and window and cannot be
> reconciled without choosing. Candidates: +20% in 10 bars · +30% in 20 bars ·
> +30% in 60 bars · +50% in 30 bars · +100% in 63 bars · no requirement.

Eleven concepts, 40+ candidate definitions. The Hypothesis Lab backtests them
against each other and reports whether the concept survives the choice.

### Strategy engine
A strategy is **data, never code** — a validated `StrategySpec` document. A
user-authored strategy cannot execute anything. Versions are immutable; changes
bump the version and record the reason. Six formalised strategies ship, each
carrying per-rule provenance:

| Key | What it is |
|---|---|
| `sar_v1_0` | Literal reading of the SAR five-step breakout |
| `sar_v1_1_adr_stop` | …plus Kullamägi's 1× ADR stop cap and time-based scale-out |
| `sar_no_market_filter_v1_0` | Control arm: index filter off |
| `sar_trail20_v1_0` | Conflict arm: 20 SMA trail instead of 10 |
| `kullamagi_breakout_v1_0` | Continuation breakout from the public material |
| `episodic_pivot_v0_1` | **Deliberately incomplete** — see below |

### Backtesting
Event-driven daily simulation with the assumptions written down and defended:

- Signals read bars `<= t`; the default fill is the **next open**.
- A stop **does not fill at the stop price when the market gaps through it** — it
  fills at the open.
- When a bar's range contains both the stop and a target, **the stop is assumed
  first**. Daily bars cannot tell us the order, and the optimistic assumption is
  how backtests lie.
- Slippage and commission are charged on every fill, including partials.
- Position sizing, max positions, max position %, portfolio exposure, partial
  exits, breakeven moves, SMA/EMA/chandelier trailing, time stops, max hold.
- 30+ performance metrics, plus a **rejection breakdown** showing which filter
  removed every candidate that did not become a trade.

### Overfitting defences
- **Walk-forward** with anchored rolling train/test windows, parameter-stability
  reporting, and a degradation figure.
- **Sensitivity sweeps** that label a result `FRAGILE` when the surface is a
  spike rather than a plateau.
- **Monte Carlo** (bootstrap and order-shuffle) with percentile equity bands,
  drawdown and losing-streak distributions — labelled `MODEL_ESTIMATE` with an
  explicit "this is not a prediction" caveat.
- Sample-size verdicts everywhere; `INSUFFICIENT_DATA` is a first-class answer.

### Statistical engine
Hand-rolled so every method used to justify a trading claim is visible:
Wilson score intervals, exact binomial tests, percentile bootstrap, two-sided
permutation tests, Welch's t, Cohen's d. Verdicts are drawn from
`SUPPORTED / MIXED / UNSUPPORTED / INSUFFICIENT_DATA` — and split experiments are
**direction-aware**: a statistically detectable effect pointing *against* the
hypothesis is reported as evidence against the source, not as confirmation.

### Historical analogs ("statistical odds")
Finds every historical bar matching a setup definition and reports forward
returns at 1/3/5/10/20 days with confidence intervals, MFE/MAE distributions, and
the probability of reaching a target before the stop — with the sample size on
every line and the overlapping-events caveat attached.

### Market regime, sector and theme engines
Regime from index MA structure, drawdown, realised volatility and its percentile,
plus breadth. Sectors and themes ranked from **six** measurements converted to
cross-group percentiles — never today's percentage gain — with every component
shown next to the composite.

### Screener (server-side, whole market)
One indexed SQL statement over precomputed features — no vendor calls, no
recomputation. 40+ filters covering price, liquidity, relative volume, ATR/ADR,
RSI, all four SMAs and distances from them, prior moves over five horizons,
consolidation length/depth/contraction, breakout proximity and volume, relative
strength, sector and theme rank, and market regime. A `SAR preset` encodes the
strategy screen. Filters the system cannot honour (market cap, earnings
proximity, options liquidity) are **refused with the reason**, never silently
dropped. Every run stores its exact configuration and a hash.

### Scanner and ranking
Eight weighted components (regime, sector, theme, relative strength, prior
momentum, base quality, volume pattern, breakout quality). Every candidate
carries the raw measurement, the sub-score, the weight and the contribution, so
"NVDA 73.9" unpacks into the eight numbers that produced it. Pre-breakout
("approaching") candidates are found too, not only triggered ones.

### Trade Analyzer + anti-bias
Verdict (`HIGH-QUALITY / MODERATE / LOW / NO TRADE`) computed **mechanically**
from the evidence, so it cannot be talked into agreeing. Always includes a
populated **bear case** and an **invalidation** section generated from the same
measurements as the bull case. It will tell you not to take the trade.

### Options Lab
Black-Scholes pricing, full Greeks, IV solving, contract comparison under a
common move, and a scenario matrix across price × time × IV shift. When no
options provider is reachable it generates a **model chain** — stamped
`MODEL_ESTIMATE` on every contract, with volume and open interest shown as `--`
rather than invented.

### Trade Journal + Personal Trading Brain
Full trade records, CSV import, and behavioural analysis that only makes claims
the journal supports: hold-time asymmetry (cut winners / let losers run) via a
permutation test, position-sizing discipline, overtrading, per-setup and
per-regime expectancy with Wilson intervals — and an explicit "not testable yet"
when a field is missing.

### AI Brain
Two modes over one tool registry of 32 tools:

- **router** (always available, no key, no network): parses intent, calls tools,
  renders the answer from what came back. It cannot hallucinate because it has no
  generative component.
- **llm** (`ANTHROPIC_API_KEY`): an Anthropic tool-use loop over the *same*
  tools, under a system prompt that forbids producing numbers.

Either way, the tool-call transcript is returned with the answer so every figure
traces to the call that produced it.

---

## Where the knowledge came from, and what is unverified

The build environment blocks HTTPS to non-package hosts, so first-party pages
could not be fetched in full. Web *search* worked. Consequently:

- Every source record carries `retrieval_method` and `fulltext_verified`.
- Records summarised from search results are marked **UNVERIFIED** in the UI and
  **store no verbatim quote**. The `observation` field says what search results
  attribute to the source; it never asserts the claim is true.
- The operator's own strategy brief is the one fully verified source.

`python3 -m tradingbrain.cli research refresh` fetches the first-party pages when
network access is available, stores a citation-length excerpt (600 chars max —
never a whole work), and flips the verified flag. It records 401/402/403 as
inaccessible; paywalls and logins are never circumvented.

Sources registered: [sartrading.io/strategy](https://www.sartrading.io/strategy),
[qullamaggie.com](https://qullamaggie.com/my-3-timeless-setups-that-have-made-me-tens-of-millions/),
[qullamaggie.com episodic pivots](https://qullamaggie.com/how-to-master-a-setup-episodic-pivots/),
plus third-party interview notes, ranked below first-party material and labelled as such.

## Knowledge conflicts, preserved

When sources disagree the disagreement *is* the finding. Four are recorded, each
with both positions intact, a runnable strategy arm per side, and an experiment
that adjudicates for **this operator on this data** rather than universally:

- 10 SMA vs 20 SMA trailing
- Partial at +5R (SAR) vs one-third after 3–5 days (Kullamägi)
- Index 10>20 MA filter vs "uptrending or sideways is fine"
- 2–5% stop vs 1× ADR stop

## What is deliberately NOT built

Stated plainly rather than faked:

- **Episodic pivots are incomplete (`v0.1`).** The setup is defined by a
  *catalyst*. Without a point-in-time news/earnings feed the catalyst cannot be
  identified, so the spec captures only the price signature — a strict superset.
  Statistics from it describe "large gap-ups", and say so.
- **The parabolic short is not implemented.** Shorting has different borrow, gap
  and assignment risk; implementing it as a sign flip of the long engine would be
  wrong.
- **Opening-range entry is not tested.** The rule is recorded and the entry type
  exists, but no intraday data source is reachable, so a comparison would run on
  generated intraday bars and prove nothing. Recorded as *blocked*, not as
  *unsupported*.
- **No fundamentals provider.** Market cap, float and shares outstanding are not
  derivable from bars, so those screener filters are refused with the reason
  rather than approximated.
- **No earnings calendar.** "Days to earnings" would be a guess, so the filter is
  refused. Finnhub would supply it; the protocol slot exists.
- **Consolidated (SIP) data is a subscription, not a code change.** The adapter
  and every label already handle it; the free tier gives IEX and says so.
- **No broker integration.** Market data and execution are separate layers by
  design (`MarketDataProvider` vs `BrokerProvider`). Live trading is off by
  default and cannot be enabled by configuration alone: any adapter must pass
  every check in `risk/guards.py`, including a per-order confirmation token.
- **Alert delivery is in-app only.** Email/Discord/Telegram/SMS/browser channels
  are declared with what each needs; a channel that silently does nothing is
  worse than one that says it is not configured.

---

## Command line

```
python3 -m tradingbrain.cli serve                    # the web app
python3 -m tradingbrain.cli data bootstrap           # universe + history + features
python3 -m tradingbrain.cli data status              # coverage, freshness, API usage
python3 -m tradingbrain.cli data sync                # incremental: only what is new
python3 -m tradingbrain.cli data backfill NVDA AAPL --start 2015-01-01
python3 -m tradingbrain.cli data features            # recompute indicators + features
python3 -m tradingbrain.cli data screen --preset sar # SQL screener
python3 -m tradingbrain.cli data validate            # re-run every quality check
python3 -m tradingbrain.cli data stream              # realtime WebSocket ingestion
python3 -m tradingbrain.cli data datasets            # locked dataset versions
python3 -m tradingbrain.cli providers                # which data path is active
python3 -m tradingbrain.cli seed                     # knowledge, concepts, hypotheses
python3 -m tradingbrain.cli regime | sectors | scan
python3 -m tradingbrain.cli analyze NVDA --equity 50000 --risk 1
python3 -m tradingbrain.cli backtest sar_v1_1_adr_stop --start 2015-01-01 --montecarlo
python3 -m tradingbrain.cli research run --kind split --feature volume_contraction
python3 -m tradingbrain.cli research why "volume dry-up"
python3 -m tradingbrain.cli research conflicts
python3 -m tradingbrain.cli ask "why should I not take NVDA?"
python3 -m tradingbrain.cli demo                     # exercise every engine once
python3 -m tradingbrain.cli demo-journal --clear     # fill the journal with SAMPLE trades
```

## Tests

```
python3 -m pytest            # 219 unit tests, ~7s
python3 -m pytest -m slow    # 16 end-to-end tests over the generated universe, ~54s
```

The tests that matter most:

- `test_indicators.py::test_point_in_time_invariant` — every indicator's value at
  bar `i` is unchanged when future bars are removed.
- `test_backtest.py::test_no_lookahead_*` — trades settled before a cut date are
  byte-identical with and without future data.
- `test_backtest.py::test_gap_through_the_stop_fills_at_the_open`
- `test_structure.py::test_vectorised_scan_matches_the_scalar_detector` — the
  fast path and the readable path agree bar for bar.
- `test_integration.py::test_equity_change_reconciles_with_trade_pnl`
- `test_research.py::test_unverified_sources_store_no_quote`
- `test_market_data.py::test_backfill_then_incremental_asks_for_nothing` — a
  covered range costs zero vendor calls.
- `test_market_data.py::test_a_second_provider_cannot_silently_overwrite`
- `test_market_data.py::test_changing_the_data_changes_the_dataset_id`
- `test_websocket.py` — the hand-rolled WebSocket client against a real server:
  masking, fragmentation, ping/pong, close codes, abrupt disconnect.
- `test_alpaca.py::test_iex_is_never_described_as_full_market`
- `test_integration.py::test_engines_read_the_store_without_spending_api_calls`
- `test_integration.py::test_scanner_fast_and_slow_paths_agree` — the
  precomputed path means the same thing as recomputing from bars.

## Configuration

Everything is environment variables; nothing secret is committed. See
`.env.example` for each key and what it unlocks. Live trading defaults:

```
BRAIN_LIVE_TRADING_ENABLED=false
BRAIN_KILL_SWITCH=false
BRAIN_MAX_DAILY_LOSS_PCT=3.0
BRAIN_MAX_POSITION_PCT=20.0
BRAIN_MAX_ORDER_NOTIONAL=5000
```

## Layout

```
brain/
  tradingbrain/
    provenance.py       evidence classes, Claim, phrasing rules
    config.py           settings from the environment
    data/               types, panel, quality, providers/ (alpaca, wsclient, …)
    marketstore/        schema.sql · store.py · calendar.py · repository.py
    ingest/             service · realtime · features · derived
    screener/           sql_screener.py
    indicators/         core.py (vectorised) · structure.py (setup detectors)
    strategy/           spec.py (data, not code) · evaluator.py · library.py
    backtest/           engine · metrics · walkforward · montecarlo · sensitivity
    stats/              core.py (hand-rolled) · analogs.py
    regime/  sectors/  scanner/  options/  risk/  journal/  alerts/
    research/           knowledge · formalizer · hypothesis · conflicts · sources/
    ai/                 tools.py (32 tools) · orchestrator.py · analyzer.py
    api/app.py          stdlib HTTP server
    cli.py
  web/                  dark research terminal, no build step, no CDN
  tests/                235 tests
```

## The mindset this encodes

The system is built to keep asking: *What is the sample size? Does it work out of
sample? Does the edge survive costs? Does it disappear if a parameter moves 10%?
Could survivorship or look-ahead explain it? Is this a real effect or noise?*

When the honest answer is "we cannot tell from this", it says that.
