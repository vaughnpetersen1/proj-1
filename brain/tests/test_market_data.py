"""Market-data infrastructure: store, calendar, ingestion, features, screener.

These tests are the reason to believe the claim "the database is the source of
truth": they show that bars are not re-fetched, that a provider seam cannot form
silently, that a backtest pins the bytes it ran on, and that the screener reads
rather than recomputes.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

import numpy as np
import pytest

from tradingbrain.data.types import Bar, Timeframe
from tradingbrain.marketstore import calendar as cal
from tradingbrain.marketstore.store import MarketStore, table_for


# --------------------------------------------------------------- calendar --

def test_calendar_matches_known_nyse_session_counts():
    assert cal.count_trading_days(dt.date(2023, 1, 1), dt.date(2023, 12, 31)) == 250
    assert cal.count_trading_days(dt.date(2024, 1, 1), dt.date(2024, 12, 31)) == 252
    assert cal.count_trading_days(dt.date(2025, 1, 1), dt.date(2025, 12, 31)) == 250


def test_good_friday_and_juneteenth():
    assert not cal.is_trading_day(dt.date(2024, 3, 29))
    assert not cal.is_trading_day(dt.date(2025, 4, 18))
    assert not cal.is_trading_day(dt.date(2023, 6, 19))
    assert cal.is_trading_day(dt.date(2021, 6, 18))


def test_observed_holidays_shift_off_the_weekend():
    assert not cal.is_trading_day(dt.date(2021, 7, 5))
    assert not cal.is_trading_day(dt.date(2027, 12, 24))


def test_unscheduled_closures_are_excluded():
    assert not cal.is_trading_day(dt.date(2012, 10, 29))
    assert not cal.is_trading_day(dt.date(2025, 1, 9))


# ------------------------------------------------------------ market store --

@pytest.fixture
def store(tmp_path):
    return MarketStore(tmp_path / "market.db")


def _bars(start: dt.date, n: int, base: float = 100.0) -> list[Bar]:
    out, d = [], start
    for i in range(n):
        while not cal.is_trading_day(d):
            d += dt.timedelta(days=1)
        px = base + i
        out.append(Bar(dt.datetime.combine(d, dt.time(16, 0)), px, px + 1, px - 1,
                       px + 0.5, 1_000_000 + i))
        d += dt.timedelta(days=1)
    return out


def test_bars_round_trip_with_provenance(store):
    store.upsert_bars("AAPL", "1d", _bars(dt.date(2024, 1, 2), 10), "alpaca", "iex")
    s = store.bars("AAPL")
    assert len(s) == 10
    assert s.provider == "alpaca"
    assert s.origin.value == "REAL"
    assert "feed=iex" in s.warnings


def test_reingesting_the_same_bars_updates_rather_than_duplicates(store):
    bars = _bars(dt.date(2024, 1, 2), 10)
    a = store.upsert_bars("AAPL", "1d", bars, "alpaca", "iex")
    b = store.upsert_bars("AAPL", "1d", bars, "alpaca", "iex")
    assert a["inserted"] == 10 and a["updated"] == 0
    assert b["inserted"] == 0 and b["updated"] == 10
    assert store.coverage("AAPL")["rows"] == 10


def test_a_second_provider_cannot_silently_overwrite(store):
    bars = _bars(dt.date(2024, 1, 2), 10)
    store.upsert_bars("AAPL", "1d", bars, "alpaca", "iex")
    res = store.upsert_bars("AAPL", "1d", bars, "polygon", "sip")
    assert res["conflicts"] == 10 and res["rows"] == 0
    assert store.bars("AAPL").provider == "alpaca"
    assert any("different provider" in log["detail"] for log in store.logs())


def test_provider_override_is_possible_but_explicit(store):
    bars = _bars(dt.date(2024, 1, 2), 5)
    store.upsert_bars("AAPL", "1d", bars, "alpaca", "iex")
    res = store.upsert_bars("AAPL", "1d", bars, "polygon", "sip",
                            allow_provider_override=True)
    assert res["conflicts"] == 0 and res["updated"] == 5
    assert store.bars("AAPL").provider == "polygon"


def test_a_fully_rejected_import_does_not_relabel_the_series(store):
    store.upsert_bars("AAPL", "1d", _bars(dt.date(2024, 1, 2), 5), "alpaca", "iex")
    store.upsert_bars("AAPL", "1d", _bars(dt.date(2024, 1, 2), 5), "polygon", "sip")
    assert store.freshness("1d")["primary_provider"] == "alpaca"


def test_missing_ranges_ignore_weekends_and_holidays(store):
    store.upsert_bars("AAPL", "1d", _bars(dt.date(2024, 7, 1), 3), "alpaca", "iex")
    missing = store.missing_daily_ranges("AAPL", dt.date(2024, 7, 1), dt.date(2024, 7, 10))
    flat = [d for lo, hi in missing for d in cal.trading_days(lo, hi)]
    assert dt.date(2024, 7, 4) not in flat
    assert dt.date(2024, 7, 6) not in flat
    assert dt.date(2024, 7, 5) in flat


def test_missing_ranges_are_merged_into_contiguous_blocks(store):
    missing = store.missing_daily_ranges("NEW", dt.date(2024, 1, 2), dt.date(2024, 1, 31))
    assert len(missing) == 1
    assert missing[0][0] == dt.date(2024, 1, 2)


def test_missing_ranges_are_empty_when_fully_covered(store):
    store.upsert_bars("AAPL", "1d", _bars(dt.date(2024, 1, 2), 20), "alpaca", "iex")
    end = store.coverage("AAPL")["last"]
    assert store.missing_daily_ranges("AAPL", dt.date(2024, 1, 2),
                                      dt.date.fromisoformat(end)) == []


def test_freshness_reports_feed_and_age(store):
    store.upsert_bars("AAPL", "1d", _bars(dt.date(2024, 1, 2), 5), "alpaca", "iex")
    f = store.freshness("1d")
    assert f["symbols_tracked"] == 1
    assert f["primary_feed"] == "iex"
    assert f["last_bar_age_minutes"] > 0


def test_derived_tables_are_droppable_and_raw_is_not_touched(store):
    store.upsert_bars("AAPL", "1d", _bars(dt.date(2024, 1, 2), 10), "alpaca", "iex")
    store.upsert_strategy_features("AAPL", [{"ts": 1704200400, "close": 1.0}])
    assert store.stats()["row_counts"]["strategy_features"] == 1
    store.clear_derived("all")
    assert store.stats()["row_counts"]["strategy_features"] == 0
    assert store.stats()["row_counts"]["market_data_daily"] == 10


def test_nan_never_reaches_the_database(store):
    store.upsert_strategy_features("AAPL", [{"ts": 1, "close": float("nan"),
                                             "base_quality": float("inf"),
                                             "prior_move_pct": 3.5}])
    row = store.query("SELECT close, base_quality, prior_move_pct FROM strategy_features")[0]
    assert row["close"] is None and row["base_quality"] is None
    assert row["prior_move_pct"] == 3.5


def test_symbols_and_sectors(store):
    store.upsert_symbols([{"symbol": "AAPL", "name": "Apple", "sector": "Technology"},
                          {"symbol": "XOM", "sector": "Energy"}], source="test")
    assert store.symbol_list() == ["AAPL", "XOM"]
    assert store.sector_map()["AAPL"] == "Technology"
    store.deactivate_symbol("XOM", "2024-06-01")
    assert store.symbol_list() == ["AAPL"]


def test_corporate_actions_dedupe(store):
    a = {"ex_date": "2024-06-10", "action_type": "forward_split", "ratio": 10.0}
    store.upsert_corporate_actions("NVDA", [a], "alpaca")
    store.upsert_corporate_actions("NVDA", [a], "alpaca")
    assert len(store.corporate_actions("NVDA")) == 1


def test_unknown_timeframe_is_rejected():
    with pytest.raises(KeyError):
        table_for("3h")


# --------------------------------------------------------- dataset locking --

def test_dataset_lock_is_deterministic_and_reused(store):
    store.upsert_bars("AAPL", "1d", _bars(dt.date(2024, 1, 2), 10), "alpaca", "iex")
    kw = dict(symbols=["AAPL"], timeframe="1d", start=None, end=None,
              provider="alpaca", feed="iex", origin="REAL",
              adjustment="split_and_dividend")
    a = store.lock_dataset(**kw)
    b = store.lock_dataset(**kw)
    assert a["id"] == b["id"] and b["reused"] is True
    assert a["row_count"] == 10


def test_changing_the_data_changes_the_dataset_id(store):
    store.upsert_bars("AAPL", "1d", _bars(dt.date(2024, 1, 2), 10), "alpaca", "iex")
    kw = dict(symbols=["AAPL"], timeframe="1d", start=None, end=None,
              provider="alpaca", feed="iex", origin="REAL", adjustment="none")
    a = store.lock_dataset(**kw)
    store.upsert_bars("AAPL", "1d", _bars(dt.date(2024, 1, 2), 10, base=50.0),
                      "alpaca", "iex", allow_provider_override=True)
    b = store.lock_dataset(**kw)
    assert a["id"] != b["id"]
    assert a["checksum"] != b["checksum"]


# ------------------------------------------------------------- aggregation --

def test_aggregate_bars_anchors_to_the_session_open():
    from tradingbrain.ingest.service import aggregate_bars
    bars = [Bar(dt.datetime(2024, 1, 2, 9, 30) + dt.timedelta(minutes=i),
                100 + i, 101 + i, 99 + i, 100.5 + i, 1000) for i in range(20)]
    five = aggregate_bars(bars, 300)
    assert len(five) == 4
    assert five[0].ts.time() == dt.time(9, 30)
    assert five[0].open == 100 and five[0].close == 104.5
    assert five[0].high == 105 and five[0].low == 99
    assert all(b.volume == 5000 for b in five)


def test_aggregate_bars_does_not_merge_across_days():
    from tradingbrain.ingest.service import aggregate_bars
    bars = ([Bar(dt.datetime(2024, 1, 2, 15, 58) + dt.timedelta(minutes=i),
                 1, 1, 1, 1, 1) for i in range(2)]
            + [Bar(dt.datetime(2024, 1, 3, 9, 30), 2, 2, 2, 2, 1)])
    out = aggregate_bars(bars, 300)
    assert len({b.ts.date() for b in out}) == 2


# ------------------------------------------------------------- ingestion ---

@pytest.fixture
def ingest_env(tmp_path):
    from tradingbrain.config import Settings
    from tradingbrain.data.providers.base import HistoricalDataProvider
    from tradingbrain.data.types import PriceSeries
    from tradingbrain.ingest.service import IngestionService
    from tradingbrain.provenance import DataOrigin

    calls: list[tuple] = []

    class FakeVendor(HistoricalDataProvider):
        name = "fakevendor"
        feed = "test-feed"
        synthetic = False

        def available(self):
            return True, "fake"

        def symbols(self):
            return ["AAA", "BBB"]

        def daily(self, symbol, start=None, end=None):
            calls.append((symbol, start, end))
            days = cal.trading_days(start or dt.date(2024, 1, 2),
                                    end or dt.date(2024, 3, 1))
            bars = [Bar(dt.datetime.combine(d, dt.time(16, 0)), 100 + i, 101 + i,
                        99 + i, 100.5 + i, 1e6) for i, d in enumerate(days)]
            return PriceSeries.from_bars(symbol, Timeframe.D1, bars, provider=self.name,
                                         origin=DataOrigin.REAL, adjusted=True)

    store = MarketStore(tmp_path / "m.db")
    settings = Settings()
    settings.market_db_path = tmp_path / "m.db"
    settings.ingest_provider_order = ("fakevendor",)
    vendor = FakeVendor()
    svc = IngestionService(store, settings, provider_lookup=lambda n: vendor)
    return svc, store, calls


def test_backfill_then_incremental_asks_for_nothing(ingest_env):
    svc, store, calls = ingest_env
    r1 = svc.backfill(["AAA"], "1d", dt.date(2024, 1, 2), dt.date(2024, 3, 1))
    assert r1["ok"] and r1["rows_written"] > 30
    n_after_first = len(calls)
    r2 = svc.backfill(["AAA"], "1d", dt.date(2024, 1, 2), dt.date(2024, 3, 1))
    assert r2["rows_written"] == 0
    assert r2["up_to_date"] == 1
    assert len(calls) == n_after_first, "a covered range must not be re-requested"


def test_backfill_requests_only_the_missing_tail(ingest_env):
    svc, store, calls = ingest_env
    svc.backfill(["AAA"], "1d", dt.date(2024, 1, 2), dt.date(2024, 2, 1))
    calls.clear()
    svc.backfill(["AAA"], "1d", dt.date(2024, 1, 2), dt.date(2024, 3, 1))
    assert len(calls) == 1
    _, start, _ = calls[0]
    assert start > dt.date(2024, 1, 30), f"asked from {start}, expected only the tail"


def test_ingestion_records_provider_and_feed(ingest_env):
    svc, store, _ = ingest_env
    svc.backfill(["AAA"], "1d", dt.date(2024, 1, 2), dt.date(2024, 2, 1))
    status = store.sync_status()[0]
    assert status["provider"] == "fakevendor"
    assert status["feed"] == "test-feed"
    assert status["status"] == "ok"


def test_bad_bars_are_rejected_on_ingest_not_stored(tmp_path):
    from tradingbrain.config import Settings
    from tradingbrain.data.providers.base import HistoricalDataProvider
    from tradingbrain.data.types import PriceSeries
    from tradingbrain.ingest.service import IngestionService
    from tradingbrain.provenance import DataOrigin

    class BrokenVendor(HistoricalDataProvider):
        name = "broken"
        feed = None
        synthetic = False

        def available(self):
            return True, "fake"

        def daily(self, symbol, start=None, end=None):
            bars = _bars(dt.date(2024, 1, 2), 30)
            bad = bars[5]
            bars[5] = Bar(bad.ts, bad.open, bad.low - 5, bad.high + 5, bad.close, bad.volume)
            return PriceSeries.from_bars(symbol, Timeframe.D1, bars, provider=self.name,
                                         origin=DataOrigin.REAL, adjusted=True)

    store = MarketStore(tmp_path / "m.db")
    settings = Settings()
    settings.ingest_provider_order = ("broken",)
    svc = IngestionService(store, settings, provider_lookup=lambda n: BrokenVendor())
    r = svc.backfill(["AAA"], "1d", dt.date(2024, 1, 2), dt.date(2024, 3, 1))
    assert r["rows_written"] == 0
    assert r["errors"]
    assert store.coverage("AAA")["rows"] == 0
    assert any(f["code"] == "ohlc_inconsistent" for f in store.flags())


def test_provider_fallback_reports_why_each_was_skipped(tmp_path):
    from tradingbrain.config import Settings
    from tradingbrain.ingest.service import IngestionService
    store = MarketStore(tmp_path / "m.db")
    settings = Settings()
    settings.ingest_provider_order = ("nope1", "nope2")

    def missing(name):
        raise KeyError(name)

    svc = IngestionService(store, settings, provider_lookup=missing)
    r = svc.backfill(["AAA"], "1d")
    assert not r["ok"]
    assert len(r["tried"]) == 2


def test_calendar_gaps_are_flagged(ingest_env):
    from tradingbrain.data.types import PriceSeries
    from tradingbrain.provenance import DataOrigin
    svc, store, _ = ingest_env
    days = cal.trading_days(dt.date(2024, 1, 2), dt.date(2024, 2, 15))
    del days[10]
    bars = [Bar(dt.datetime.combine(d, dt.time(16, 0)), 100, 101, 99, 100, 1e6)
            for d in days]
    series = PriceSeries.from_bars("AAA", Timeframe.D1, bars, provider="x",
                                   origin=DataOrigin.REAL)
    svc._flag_calendar_gaps("AAA", series)
    flags = [f for f in store.flags() if f["code"] == "missing_trading_days"]
    assert flags and "1 of" in flags[0]["detail"]


# ------------------------------------------------------------- features ----

@pytest.fixture
def featured(ingest_env):
    from tradingbrain.ingest.features import FeatureEngine
    svc, store, _ = ingest_env
    svc.backfill(["AAA", "BBB"], "1d", dt.date(2024, 1, 2), dt.date(2024, 12, 31))
    fe = FeatureEngine(store)
    fe.compute_universe(["AAA", "BBB"])
    return store, fe


def test_features_are_written_and_versioned(featured):
    store, fe = featured
    cov = store.feature_coverage()
    assert cov["symbols"] == 2 and cov["rows"] > 100
    row = store.query("SELECT feature_version, config_hash FROM strategy_features LIMIT 1")[0]
    assert row["feature_version"] == 1
    assert row["config_hash"]


def test_cross_sectional_percentiles_are_filled(featured):
    store, _ = featured
    rows = store.query("SELECT relative_strength_pct FROM strategy_features "
                       "WHERE relative_strength_pct IS NOT NULL LIMIT 50")
    assert rows
    assert all(0 <= r["relative_strength_pct"] <= 100 for r in rows)


def test_features_at_returns_the_latest_row_per_symbol(featured):
    store, _ = featured
    rows = store.features_at(dt.date(2024, 12, 31))
    assert {r["symbol"] for r in rows} == {"AAA", "BBB"}
    assert len(rows) == 2


# ------------------------------------------------------------- screener ----

def test_screener_filters_and_stores_its_configuration(featured):
    from tradingbrain.screener.sql_screener import SqlScreener
    store, _ = featured
    sc = SqlScreener(store)
    r = sc.run({"min_price": 0.0}, limit=10)
    assert r["ok"] and r["matched"] >= 1
    assert r["run_id"]
    run = store.one("SELECT config, config_hash FROM scanner_runs WHERE id=?",
                    (r["run_id"],))
    assert json.loads(run["config"])["filters"] == {"min_price": 0.0}
    assert run["config_hash"]


def test_screener_narrows_with_a_tighter_filter(featured):
    from tradingbrain.screener.sql_screener import SqlScreener
    store, _ = featured
    sc = SqlScreener(store)
    loose = sc.run({"min_price": 0.0}, limit=100, save=False)["matched"]
    tight = sc.run({"min_price": 1e9}, limit=100, save=False)["matched"]
    assert tight == 0 and loose > 0


def test_screener_refuses_filters_it_cannot_honour(featured):
    from tradingbrain.screener.sql_screener import SqlScreener
    store, _ = featured
    r = SqlScreener(store).run({"min_market_cap": 1e9, "max_days_to_earnings": 5},
                               limit=5, save=False)
    assert set(r["unavailable_filters"]) == {"min_market_cap", "max_days_to_earnings"}
    assert "fundamentals provider" in r["unavailable_filters"]["min_market_cap"]


def test_screener_says_so_when_no_features_exist(tmp_path):
    from tradingbrain.screener.sql_screener import SqlScreener
    store = MarketStore(tmp_path / "empty.db")
    r = SqlScreener(store).run({"min_price": 1.0})
    assert not r["ok"]
    assert "features" in r["reason"]


def test_screener_context_filter_reports_when_it_cannot_apply(featured):
    from tradingbrain.screener.sql_screener import SqlScreener
    store, _ = featured
    r = SqlScreener(store).run({"require_market_regimes": ["BULL"]}, limit=5, save=False)
    assert any("regime" in n for n in r["context_notes"])
