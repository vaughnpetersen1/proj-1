"""Server-side screener over precomputed features.

The rule this module exists to enforce: **opening the screener must not make
thousands of API calls, and must not recompute a 200-bar moving average per
symbol per request.** It runs one indexed SQL statement against
``strategy_features`` and ``technical_indicators``, both of which the feature
engine populated on a schedule.

Filters whose inputs the system does not have are NOT silently dropped and NOT
faked. They are listed in ``UNAVAILABLE_FILTERS`` with what each one needs, and
requesting one returns an explicit refusal.

Every run stores its exact configuration in ``scanner_runs.config`` together with
a hash, so a screen can be reproduced or diffed against a later one.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import time
from typing import Any, Iterable

from ..marketstore.store import MARKET, MarketStore

#: Filters the operator asked for that need data this build cannot obtain.
#: Requesting one is an error with a remedy, never a silently ignored field.
UNAVAILABLE_FILTERS: dict[str, str] = {
    "market_cap": ("needs shares outstanding from a fundamentals provider. Set "
                   "FINNHUB_API_KEY and implement FundamentalDataProvider.fundamentals; "
                   "until then market cap is not derivable from bars."),
    "earnings_proximity": ("needs an earnings calendar. Alpaca does not supply one; "
                           "Finnhub does. Without it, 'days to earnings' would be a guess."),
    "options_liquidity": ("needs an options chain in the store. Run "
                          "`cli data options <SYMBOL>` with an options provider "
                          "configured; the filter activates once options_contracts "
                          "holds rows for the scan date."),
    "float_shares": "needs a fundamentals provider; not derivable from price data.",
    "short_interest": "needs a short-interest feed; no configured provider supplies it.",
}


@dataclasses.dataclass
class ScreenFilters:
    """Every field is optional. None means "do not filter on this"."""

    # liquidity / price
    min_price: float | None = None
    max_price: float | None = None
    min_volume: float | None = None
    min_avg_dollar_volume: float | None = None
    min_relative_volume: float | None = None
    max_relative_volume: float | None = None

    # volatility
    min_adr_pct: float | None = None
    max_adr_pct: float | None = None
    min_atr: float | None = None

    # momentum
    min_rsi: float | None = None
    max_rsi: float | None = None
    min_prior_5d_return: float | None = None
    min_prior_10d_return: float | None = None
    min_prior_20d_return: float | None = None
    min_prior_60d_return: float | None = None
    min_prior_move_pct: float | None = None
    max_prior_move_pct: float | None = None

    # trend structure
    require_above_10sma: bool | None = None
    require_above_20sma: bool | None = None
    require_above_50sma: bool | None = None
    require_above_200sma: bool | None = None
    require_sma_stack: bool | None = None
    max_distance_from_10sma: float | None = None
    max_distance_from_20sma: float | None = None
    min_distance_from_20sma: float | None = None
    max_pct_from_52w_high: float | None = None

    # base / breakout
    min_consolidation_days: int | None = None
    max_consolidation_days: int | None = None
    max_consolidation_depth_pct: float | None = None
    max_range_contraction: float | None = None
    max_volume_contraction: float | None = None
    min_base_quality: float | None = None
    require_base_qualifies: bool | None = None
    max_breakout_distance_pct: float | None = None
    require_breakout_today: bool | None = None
    min_breakout_volume_ratio: float | None = None
    min_close_position: float | None = None

    # relative / group strength
    min_relative_strength_pct: float | None = None
    min_market_relative_strength: float | None = None
    min_sector_relative_strength: float | None = None
    min_sector_rank_percentile: float | None = None
    min_theme_rank_percentile: float | None = None
    sectors: list[str] | None = None
    exclude_sectors: list[str] | None = None
    symbols: list[str] | None = None

    # market context
    require_market_regimes: list[str] | None = None
    require_market_filter_pass: bool | None = None

    # requested-but-unavailable, kept so the API can refuse explicitly
    min_market_cap: float | None = None
    max_days_to_earnings: int | None = None
    min_option_open_interest: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in dataclasses.asdict(self).items() if v is not None}


#: column -> (sql expression, comparison) for the mechanical filters
_SQL_RULES: list[tuple[str, str, str]] = [
    ("min_price", "f.close", ">="), ("max_price", "f.close", "<="),
    ("min_volume", "f.volume", ">="),
    ("min_avg_dollar_volume", "f.dollar_volume_20d", ">="),
    ("min_relative_volume", "f.relative_volume", ">="),
    ("max_relative_volume", "f.relative_volume", "<="),
    ("min_adr_pct", "f.adr20", ">="), ("max_adr_pct", "f.adr20", "<="),
    ("min_atr", "f.atr14", ">="),
    ("min_rsi", "i.rsi14", ">="), ("max_rsi", "i.rsi14", "<="),
    ("min_prior_5d_return", "f.prior_5d_return", ">="),
    ("min_prior_10d_return", "f.prior_10d_return", ">="),
    ("min_prior_20d_return", "f.prior_20d_return", ">="),
    ("min_prior_60d_return", "f.prior_60d_return", ">="),
    ("min_prior_move_pct", "f.prior_move_pct", ">="),
    ("max_prior_move_pct", "f.prior_move_pct", "<="),
    ("max_distance_from_10sma", "f.distance_from_10sma", "<="),
    ("max_distance_from_20sma", "f.distance_from_20sma", "<="),
    ("min_distance_from_20sma", "f.distance_from_20sma", ">="),
    ("max_pct_from_52w_high", "f.pct_from_52w_high", "<="),
    ("min_consolidation_days", "f.consolidation_days", ">="),
    ("max_consolidation_days", "f.consolidation_days", "<="),
    ("max_consolidation_depth_pct", "f.consolidation_depth_pct", "<="),
    ("max_range_contraction", "f.range_contraction", "<="),
    ("max_volume_contraction", "f.volume_contraction", "<="),
    ("min_base_quality", "f.base_quality", ">="),
    ("max_breakout_distance_pct", "f.breakout_distance_pct", "<="),
    ("min_breakout_volume_ratio", "f.breakout_volume_ratio", ">="),
    ("min_close_position", "f.close_position", ">="),
    ("min_relative_strength_pct", "f.relative_strength_pct", ">="),
    ("min_market_relative_strength", "f.market_relative_strength", ">="),
    ("min_sector_relative_strength", "f.sector_relative_strength", ">="),
]

_BOOL_RULES: list[tuple[str, str]] = [
    ("require_above_10sma", "f.above_10sma"), ("require_above_20sma", "f.above_20sma"),
    ("require_above_50sma", "f.above_50sma"), ("require_above_200sma", "f.above_200sma"),
    ("require_sma_stack", "f.sma_stack_10_20_50"),
    ("require_base_qualifies", "f.base_qualifies"),
    ("require_breakout_today", "f.breakout_today"),
]

FILTER_CATALOG: dict[str, str] = {
    "min_price": "minimum last close",
    "max_price": "maximum last close",
    "min_volume": "minimum session volume (shares)",
    "min_avg_dollar_volume": "minimum 20-day average dollar volume -- the liquidity filter",
    "min_relative_volume": "volume vs the 50-day average, e.g. 1.5 for 150%",
    "min_adr_pct": "minimum average daily range %, Kullamagi's volatility screen",
    "min_rsi": "minimum RSI(14)", "max_rsi": "maximum RSI(14)",
    "min_prior_20d_return": "minimum 20-bar percentage return",
    "min_prior_move_pct": "minimum detected prior advance (the setup's expansion leg)",
    "require_above_10sma": "close above the 10 SMA",
    "require_sma_stack": "10 SMA > 20 SMA > 50 SMA",
    "max_distance_from_20sma": "extension cap: max % above the 20 SMA",
    "min_consolidation_days": "minimum detected base length in bars",
    "max_range_contraction": "second-half/first-half base range; below 1.0 means tightening",
    "max_volume_contraction": "second-half/first-half base volume; below 1.0 means drying up",
    "min_base_quality": "composite base score 0-1",
    "max_breakout_distance_pct": "how close price is to the base high, in percent",
    "require_breakout_today": "the breakout triggered on the scan date",
    "min_breakout_volume_ratio": "breakout-bar volume vs the prior bar",
    "min_relative_strength_pct": "cross-sectional RS percentile (0-100)",
    "min_sector_rank_percentile": "the symbol's sector must rank this high",
    "min_theme_rank_percentile": "the symbol's best theme must rank this high",
    "require_market_regimes": "only run when the stored regime label is one of these",
}

#: The SAR / Morgan strategy screen. Thresholds are the ones this system chose as
#: operationalisations, NOT values the source stated -- every one is adjustable
#: and every one is stored with the run.
SAR_PRESET: dict[str, Any] = {
    "min_price": 5.0,
    "min_avg_dollar_volume": 5_000_000.0,
    "min_prior_move_pct": 30.0,
    "require_above_10sma": True,
    "require_above_20sma": True,
    "max_range_contraction": 1.0,
    "max_volume_contraction": 1.0,
    "min_consolidation_days": 5,
    "max_consolidation_days": 40,
    "max_consolidation_depth_pct": 35.0,
    "max_breakout_distance_pct": 5.0,
    "min_close_position": 0.5,
    "min_relative_strength_pct": 50.0,
}


class SqlScreener:
    def __init__(self, store: MarketStore = MARKET) -> None:
        self.store = store

    def run(self, filters: ScreenFilters | dict[str, Any] | None = None,
            as_of: dt.date | None = None, limit: int = 100,
            timeframe: str = "1d", order_by: str = "base_quality",
            save: bool = True, strategy: str | None = None,
            themes: dict[str, list[str]] | None = None) -> dict[str, Any]:
        t0 = time.time()
        f = (filters if isinstance(filters, ScreenFilters)
             else ScreenFilters(**(filters or {})))

        refused = {k: UNAVAILABLE_FILTERS[m] for k, m in
                   (("min_market_cap", "market_cap"),
                    ("max_days_to_earnings", "earnings_proximity"),
                    ("min_option_open_interest", "options_liquidity"))
                   if getattr(f, k) is not None}

        as_of = as_of or self._latest_feature_date(timeframe)
        if as_of is None:
            return {"ok": False,
                    "reason": ("no precomputed features exist yet. Run "
                               "`python -m tradingbrain.cli data features` after a "
                               "backfill -- the screener reads features, it does not "
                               "compute them."),
                    "unavailable_filters": refused}

        where, params = self._build_where(f, as_of, timeframe)
        sql = (
            "SELECT s.symbol, s.name, s.sector, s.industry, s.exchange, f.*, "
            "  i.rsi14, i.macd, i.macd_hist, i.ema21, i.vwap "
            "FROM strategy_features f "
            "JOIN symbols s ON s.id = f.symbol_id "
            "LEFT JOIN technical_indicators i "
            "  ON i.symbol_id = f.symbol_id AND i.ts = f.ts AND i.timeframe = f.timeframe "
            "JOIN (SELECT symbol_id, MAX(ts) mts FROM strategy_features "
            "      WHERE timeframe = ? AND ts <= ? GROUP BY symbol_id) m "
            "  ON m.symbol_id = f.symbol_id AND m.mts = f.ts "
            f"WHERE {where}"
        )
        head = [timeframe, _epoch_eod(as_of)]
        rows = self.store.query(sql, head + params)

        # Context filters that are not per-row columns.
        regime = self._regime_at(as_of)
        rows, context_notes = self._apply_context(rows, f, as_of, regime, themes)

        rows.sort(key=lambda r: (-(r.get(order_by) or -1e18), r["symbol"]))
        matched = len(rows)
        rows = rows[:limit]
        for r in rows:
            r["date"] = as_of.isoformat()

        scanned = (self.store.one(
            "SELECT COUNT(DISTINCT symbol_id) n FROM strategy_features "
            "WHERE timeframe=? AND ts<=?", (timeframe, _epoch_eod(as_of))) or {}).get("n", 0)
        fresh = self.store.freshness(timeframe)
        config = {"filters": f.to_dict(), "as_of": as_of.isoformat(),
                  "timeframe": timeframe, "order_by": order_by, "limit": limit,
                  "strategy": strategy}
        run_id = None
        if save:
            run_id = self.store.save_scan(
                as_of.isoformat(), config, rows, strategy=strategy, engine="sql",
                symbols_scanned=scanned,
                data_origin=("SYNTHETIC" if any("synthetic" in (p.get("provider") or "")
                                                for p in fresh["providers"]) else "REAL"),
                provider=fresh.get("primary_provider"), feed=fresh.get("primary_feed"),
                runtime_ms=(time.time() - t0) * 1000)

        return {
            "ok": True, "run_id": run_id, "as_of": as_of.isoformat(),
            "symbols_scanned": scanned, "matched": matched,
            "returned": len(rows), "results": rows,
            "filters": f.to_dict(),
            "unavailable_filters": refused,
            "context_notes": context_notes,
            "market_regime": regime,
            "data_freshness": fresh,
            "engine": "sql over precomputed features",
            "seconds": round(time.time() - t0, 3),
            "note": ("This screen read precomputed feature rows. It made no vendor API "
                     "calls. Feature freshness is bounded by the last feature "
                     "computation, not by this request."),
        }

    # -- internals ---------------------------------------------------------
    def _latest_feature_date(self, timeframe: str) -> dt.date | None:
        r = self.store.one("SELECT MAX(ts) hi FROM strategy_features WHERE timeframe=?",
                           (timeframe,))
        if not r or not r.get("hi"):
            return None
        return dt.datetime.fromtimestamp(r["hi"], dt.timezone.utc).date()

    def _build_where(self, f: ScreenFilters, as_of: dt.date,
                     timeframe: str) -> tuple[str, list[Any]]:
        clauses = ["f.timeframe = ?"]
        params: list[Any] = [timeframe]
        for field, column, op in _SQL_RULES:
            v = getattr(f, field)
            if v is not None:
                clauses.append(f"{column} {op} ?")
                params.append(v)
        for field, column in _BOOL_RULES:
            v = getattr(f, field)
            if v is not None:
                clauses.append(f"{column} = ?")
                params.append(int(bool(v)))
        if f.sectors:
            clauses.append("s.sector IN (" + ",".join("?" * len(f.sectors)) + ")")
            params += f.sectors
        if f.exclude_sectors:
            clauses.append("IFNULL(s.sector,'') NOT IN ("
                           + ",".join("?" * len(f.exclude_sectors)) + ")")
            params += f.exclude_sectors
        if f.symbols:
            up = [s.upper() for s in f.symbols]
            clauses.append("s.symbol IN (" + ",".join("?" * len(up)) + ")")
            params += up
        clauses.append("s.active = 1")
        return " AND ".join(clauses), params

    def _regime_at(self, as_of: dt.date) -> dict[str, Any] | None:
        return self.store.one(
            "SELECT * FROM market_regimes WHERE as_of <= ? ORDER BY as_of DESC LIMIT 1",
            (as_of.isoformat(),))

    def _apply_context(self, rows: list[dict[str, Any]], f: ScreenFilters,
                       as_of: dt.date, regime: dict[str, Any] | None,
                       themes: dict[str, list[str]] | None) -> tuple[list[dict], list[str]]:
        notes: list[str] = []

        if f.require_market_regimes or f.require_market_filter_pass is not None:
            if regime is None:
                notes.append("Market-regime filter requested but no regime has been "
                             "computed and stored for this date; the filter was NOT "
                             "applied. Run `cli data regime` to populate it.")
            else:
                if f.require_market_regimes and regime["label"] not in f.require_market_regimes:
                    notes.append(f"Regime is {regime['label']}, not in "
                                 f"{f.require_market_regimes}: no results returned.")
                    return [], notes
                if (f.require_market_filter_pass is not None
                        and bool(regime["filter_pass"]) != f.require_market_filter_pass):
                    notes.append("Index moving-average filter state does not match the "
                                 "requirement: no results returned.")
                    return [], notes

        sector_pct = self._group_percentiles(as_of, "sector")
        theme_pct = self._group_percentiles(as_of, "theme")
        for r in rows:
            r["sector_rank_percentile"] = sector_pct.get(r.get("sector"))
        if f.min_sector_rank_percentile is not None:
            if not sector_pct:
                notes.append("Sector-rank filter requested but no sector ranking is "
                             "stored for this date; the filter was NOT applied. Run "
                             "`cli data sectors`.")
            else:
                rows = [r for r in rows
                        if (r.get("sector_rank_percentile") or -1) >= f.min_sector_rank_percentile]

        if themes:
            for r in rows:
                mine = [t for t, members in themes.items() if r["symbol"] in members]
                r["themes"] = mine
                r["theme_rank_percentile"] = (max((theme_pct.get(t, 0.0) for t in mine),
                                                  default=None) if theme_pct else None)
        if f.min_theme_rank_percentile is not None:
            if not theme_pct or not themes:
                notes.append("Theme-rank filter requested but theme rankings are not "
                             "stored for this date; the filter was NOT applied.")
            else:
                rows = [r for r in rows
                        if (r.get("theme_rank_percentile") or -1) >= f.min_theme_rank_percentile]
        return rows, notes

    def _group_percentiles(self, as_of: dt.date, kind: str) -> dict[str, float]:
        rows = self.store.query(
            "SELECT name, rank FROM sector_data WHERE kind=? AND as_of=("
            "  SELECT MAX(as_of) FROM sector_data WHERE kind=? AND as_of<=?)",
            (kind, kind, as_of.isoformat()))
        n = len(rows)
        if n < 2:
            return {}
        return {r["name"]: 100.0 * (n - r["rank"]) / (n - 1) for r in rows}


def _epoch_eod(d: dt.date) -> int:
    return int(dt.datetime.combine(d, dt.time.max)
               .replace(tzinfo=dt.timezone.utc).timestamp())


def run_screen(filters: dict[str, Any] | None = None, as_of: dt.date | None = None,
               limit: int = 100, **kw: Any) -> dict[str, Any]:
    return SqlScreener().run(filters, as_of, limit, **kw)
