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
python3 -m tradingbrain.cli seed      # knowledge base, concepts, hypotheses, strategies
python3 -m tradingbrain.cli serve     # http://127.0.0.1:8787
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

## The second thing: your data path

The environment this was built in blocks outbound HTTPS to every market-data
host, so **the default data path is a clearly-labelled synthetic generator.**
Everything computed on it is stamped `SYNTHETIC` and every screen says so in
plain language. It exercises the engines; it is not evidence about markets.

To get real data in, in order of preference:

1. **CSV drop (works offline).** Export from your broker, TC2000 or Norgate to
   `data_cache/daily/<SYMBOL>.csv` with `date,open,high,low,close,volume`. The
   `csv` provider picks them up with no key and no network.
2. **An API key.** Set `TIINGO_API_KEY` or `POLYGON_API_KEY` (see `.env.example`).
3. **Free, no key.** `stooq` and `yahoo` adapters are implemented and will light
   up the moment outbound HTTPS to those hosts is permitted.

`python3 -m tradingbrain.cli providers` tells you exactly which path is active
and why each other one is not.

---

## What is actually built

### Data layer
- `MarketDataProvider` / `HistoricalDataProvider` / `IntradayDataProvider` /
  `OptionsDataProvider` / `NewsProvider` / `SectorDataProvider` interfaces —
  nothing above depends on a vendor.
- Adapters: CSV cache, Stooq, Yahoo, Tiingo, Polygon, and the synthetic generator.
- **Data-quality validation**: duplicate timestamps, OHLC inconsistency, calendar
  gaps, non-positive prices, suspected unadjusted splits, zero-volume runs,
  out-of-session intraday bars, plus a standing survivorship-bias disclosure that
  no validator can detect from bars alone.
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
- **No broker integration.** Live trading is off by default and cannot be enabled
  by configuration alone: any adapter must pass every check in
  `risk/guards.py`, including a per-order confirmation token.
- **Alert delivery is in-app only.** Email/Discord/Telegram/SMS/browser channels
  are declared with what each needs; a channel that silently does nothing is
  worse than one that says it is not configured.

---

## Command line

```
python3 -m tradingbrain.cli serve                    # the web app
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
python3 -m pytest            # 148 unit tests, ~4s
python3 -m pytest -m slow    # 11 end-to-end tests over the generated universe, ~48s
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
    data/               types, panel, quality, providers/
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
  tests/                159 tests
```

## The mindset this encodes

The system is built to keep asking: *What is the sample size? Does it work out of
sample? Does the edge survive costs? Does it disappear if a parameter moves 10%?
Could survivorship or look-ahead explain it? Is this a real effect or noise?*

When the honest answer is "we cannot tell from this", it says that.
