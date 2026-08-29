# Architecture

```
                       .env / environment variables
                                   |
                              config.py
                                   |
   +-------------------------------+-------------------------------+
   |                       PROVIDER LAYER                          |
   |  providers/{alpaca,tiingo,polygon,stooq,yahoo,csv,synthetic}  |
   |  protocols: MarketData · Historical · Intraday · Realtime     |
   |             Options · Fundamental · News · CorporateActions   |
   |             SymbolUniverse · Broker                           |
   +-------------------------------+-------------------------------+
                                   |  (only ingest/ calls these)
   +-------------------------------+-------------------------------+
   |                     INGESTION SERVICE                         |
   |  ingest/service.py    batched · gap-aware · incremental       |
   |  ingest/realtime.py   websocket · backoff · dedupe · gaps     |
   |  data/quality.py      validate BEFORE write; flag, never hide |
   +-------------------------------+-------------------------------+
                                   |
   +-------------------------------+-------------------------------+
   |                    MARKET DATABASE (market.db)                |
   |  RAW      market_data_{daily,1m,5m,15m,1h} · raw_trades       |
   |           raw_quotes · corporate_actions · options_contracts  |
   |  DERIVED  technical_indicators · strategy_features            |
   |           market_regimes · sector_data · scanner_results      |
   |  META     data_sync_status · dataset_versions · api_usage      |
   +-------------------------------+-------------------------------+
                                   |
   +-------------------------------+-------------------------------+
   |                          READ LAYER                           |
   |  marketstore/repository.DbProvider  (a provider, no network)  |
   |  -> registry.DataHub  (db first; a miss PERSISTS a backfill)  |
   |  -> types.PriceSeries (.upto(i) is the point-in-time guard)   |
   |  -> panel.Panel (cross-sectional)                             |
   +-------------------------------+-------------------------------+
                                   |
              +--------------------+--------------------+
              |                                         |
      indicators/core.py                        regime/classifier.py
      indicators/structure.py                   sectors/ranking.py
              |                                         |
              +--------------------+--------------------+
                                   |
                       strategy/spec.py  (data, not code)
                       strategy/evaluator.py  <-- ONE definition of "qualifies"
                                   |
        +--------------+-----------+-----------+--------------+
        |              |                       |              |
  backtest/engine  scanner/scan       stats/analogs      ai/analyzer
   (locks a       screener/sql_screener
        |              |                       |              |
  metrics                                statistics       verdict + bear case
  walkforward                            (stats/core)          |
  montecarlo                                  |                |
  sensitivity  ------------------------------>+----------------+
        |                                     |
        +--------------> research/hypothesis.py (the lab)
                         research/formalizer.py (concept -> definitions)
                         research/knowledge.py  (provenance chains)
                         research/conflicts.py  (disagreements preserved)
                                   |
                              db/store.py (SQLite)
                                   |
                    ai/tools.py  (32 tools -- the ONLY way to a number)
                                   |
                    ai/orchestrator.py  (router | llm)
                                   |
                       api/app.py  ->  web/  (dark terminal)
```

## Data-layer invariants

**Only `ingest/` calls a vendor.** Everything else addresses `DbProvider`, which
has no network code. A store miss does not fall through silently: the DataHub
asks the ingestion service to fetch *and persist* the range, then re-reads it, so
the same bars are never bought twice.

**Raw is never written by a calculation.** `MarketStore` exposes no method that
writes into a `market_data_*` table from an indicator. Derived tables are
droppable by design and carry `feature_version` plus a hash of the detector
parameters, so a threshold change invalidates rows instead of mixing two
definitions in one screen.

**Vendors never mix silently.** `upsert_bars` refuses to overwrite a bar another
provider supplied unless told to, and records the conflict.

**A backtest pins its bytes.** `lock_dataset` checksums every bar in scope before
the run and stores provider, feed, adjustment, universe, range and row count.

## Invariants

**1. Point-in-time.** Every function in `indicators/` satisfies *element `i` uses
only elements `<= i`*. `tests/test_indicators.py::test_point_in_time_invariant`
asserts it by recomputing on a truncated series. `PriceSeries.upto(i)` is the
sanctioned accessor. `Panel.rank_percentile` ranks row `t` only against symbols
that had data on date `t`.

**2. One definition of a setup.** The backtester, the scanner, the analyzer and
the analog search all call `strategy/evaluator.py`. If they disagreed, their
statistics would describe different things while appearing comparable.
`tests/test_integration.py::test_scanner_and_analyzer_agree_on_qualification`
guards it.

**3. Strategies are documents.** `StrategySpec` is a validated dataclass tree
with unknown-field rejection. No `eval`, no callables, no code path from a
user-authored strategy to execution.

**4. Two implementations, one meaning.** `find_consolidation` (readable, scalar)
and `scan_consolidations` (vectorised, ~200x faster) must agree bar for bar;
`tests/test_structure.py` asserts it across fixtures. The fast one is what runs;
the slow one is what you read when you want to know what the fast one means.

**5. Evidence class never changes silently.** `Claim.__post_init__` refuses to
construct a `BACKTEST_RESULT` or `LIVE_MARKET_OBSERVATION` without a named data
source, and force-appends an unremovable caveat to anything derived from
synthetic data.

**6. The LLM never produces a number.** `ai/tools.py` is the only route to a
figure. The orchestrator returns the tool-call transcript with every answer.

## Where the pessimistic assumptions live

| Decision | Choice | Why |
|---|---|---|
| Entry fill | next bar's open | daily bars cannot confirm an intrabar fill |
| Stop + target in one bar | stop assumed first | the optimistic assumption is how backtests lie |
| Gap through the stop | fills at the open | a resting stop does not protect against a gap |
| Costs on partials | charged | scale-outs are real fills |
| Universe | present-day symbols | disclosed as survivorship bias on every result |
| Sector membership | present-day | disclosed in the ranking claim |
| Bootstrap intervals | flagged optimistic | trades cluster; they are not exchangeable |
| Monte Carlo | flagged "not a prediction" | i.i.d. resampling understates real dispersion |

## Extending it

**A new data vendor** — implement the relevant protocol in
`data/providers/base.py`, register it in `registry.build_providers`, add it to
`BRAIN_PROVIDER_ORDER`. Nothing else changes.

**A new setup** — add a detector to `indicators/structure.py` (scalar + vectorised,
with the agreement test), expose its parameters on `SetupSpec`, wire the checks
into `strategy/evaluator.py`, and add a strategy to `strategy/library.py` with
its provenance.

**A new experiment kind** — add a branch to `research/hypothesis.py` and a
renderer to `web/js/views2.js::renderExperiment`. Experiments store their full
spec, so a stored one re-runs reproducibly.

**A broker** — implement `BrokerProvider`. Every order must pass
`risk/guards.py::check_order` with `live=True` *and* an explicit confirmation
token, and the guard currently refuses unconditionally because no adapter exists.
That refusal is intentional; removing it is a deliberate act, not a config change.
