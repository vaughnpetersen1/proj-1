"""Feature engine: compute once, query many times.

The screener must be able to filter the whole market without recomputing a
200-bar SMA for every symbol on every request. So indicators and strategy
features are computed here, on a schedule or on demand, and written to
``technical_indicators`` and ``strategy_features``.

Both tables are DERIVED. They are never the source of truth, they are droppable,
and every row records the feature version and the hash of the detector
parameters that produced it -- so a threshold change invalidates rows rather
than silently mixing two definitions in one screen.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import time
from typing import Any, Iterable, Sequence

import numpy as np

from ..config import SETTINGS, Settings
from ..indicators.core import (adr_pct, atr, distance_pct, dollar_volume, ema,
                               pct_change_n, relative_volume, rolling_max, rsi,
                               slope_r2, sma)
from ..indicators.structure import (PriorMoveParams, find_prior_move, scan_breakouts,
                                    scan_consolidations)
from ..marketstore.store import MARKET, MarketStore, STRATEGY_FEATURE_COLUMNS
from ..strategy.evaluator import breakout_params, consolidation_params, prior_move_params
from ..strategy.library import get_strategy
from ..strategy.spec import StrategySpec

MIN_BARS = 60


def _epoch(ts: dt.datetime) -> int:
    return int(ts.replace(tzinfo=dt.timezone.utc).timestamp())


def config_hash(spec: StrategySpec) -> str:
    payload = json.dumps({"setup": dataclasses.asdict(spec.setup),
                          "entry": dataclasses.asdict(spec.entry)}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class FeatureEngine:
    def __init__(self, store: MarketStore = MARKET, settings: Settings = SETTINGS,
                 strategy_key: str = "sar_v1_1_adr_stop") -> None:
        self.store = store
        self.settings = settings
        self.spec = get_strategy(strategy_key)
        self.strategy_key = strategy_key

    # -- per symbol --------------------------------------------------------
    def compute_symbol(self, symbol: str, timeframe: str = "1d",
                       lookback_bars: int | None = None) -> dict[str, Any]:
        series = self.store.bars(symbol, timeframe)
        n = len(series)
        if n < MIN_BARS:
            return {"symbol": symbol.upper(), "rows": 0,
                    "reason": f"only {n} bars stored; need {MIN_BARS}"}

        c, h, l, v = series.close, series.high, series.low, series.volume
        s10, s20, s50, s200 = sma(c, 10), sma(c, 20), sma(c, 50), sma(c, 200)
        ind = {
            "sma10": s10, "sma20": s20, "sma50": s50, "sma200": s200,
            "ema21": ema(c, 21), "rsi14": rsi(c, 14),
            "atr14": atr(h, l, c, 14), "adr20": adr_pct(h, l, 20),
            "relative_volume": relative_volume(v, 50),
            "dollar_volume_20d": dollar_volume(c, v, 20),
            "r2_20": slope_r2(c, 20),
        }
        macd_line = ema(c, 12) - ema(c, 26)
        signal = _ema_of(macd_line, 9)
        ind["macd"], ind["macd_signal"] = macd_line, signal
        ind["macd_hist"] = macd_line - signal
        ind["vwap"] = _rolling_vwap(h, l, c, v, 20)

        cp = consolidation_params(self.spec.setup)
        bp = breakout_params(self.spec.entry)
        base_ma = sma(c, self.spec.setup.cons_ma_len)
        trig, cons = scan_breakouts(h, l, c, v, cp, bp, base_ma, ind["relative_volume"])
        hi52 = rolling_max(h, min(252, n))
        with np.errstate(divide="ignore", invalid="ignore"):
            from_hi = np.where(hi52 > 0, (c / hi52 - 1.0) * 100.0, np.nan)
            rngb = h - l
            close_pos = np.where(rngb > 0, (c - l) / rngb, np.nan)
            prev_vol = np.concatenate([[np.nan], v[:-1]])
            brk_vol_ratio = np.where(prev_vol > 0, v / prev_vol, np.nan)

        pm_params = prior_move_params(self.spec.setup)
        start = max(0, n - lookback_bars) if lookback_bars else 0

        ind_rows: list[dict[str, Any]] = []
        feat_rows: list[dict[str, Any]] = []
        for i in range(start, n):
            ts = _epoch(series.ts[i])
            ind_rows.append({"ts": ts, **{k: arr[i] for k, arr in ind.items()}})
            if i < MIN_BARS - 1:
                continue
            base = cons.at(i)
            pm = find_prior_move(h, l, i, pm_params)
            level = base.high if base else np.nan
            feat_rows.append({
                "ts": ts,
                "close": c[i], "volume": v[i],
                "prior_5d_return": _at(pct_change_n(c, 5), i),
                "prior_10d_return": _at(pct_change_n(c, 10), i),
                "prior_20d_return": _at(pct_change_n(c, 21), i),
                "prior_60d_return": _at(pct_change_n(c, 63), i),
                "prior_120d_return": _at(pct_change_n(c, 126), i),
                "prior_move_pct": pm.pct, "prior_move_bars": pm.bars,
                "distance_from_10sma": _pct_of(c[i], s10[i]),
                "distance_from_20sma": _pct_of(c[i], s20[i]),
                "distance_from_50sma": _pct_of(c[i], s50[i]),
                "distance_from_200sma": _pct_of(c[i], s200[i]),
                "above_10sma": _above(c[i], s10[i]),
                "above_20sma": _above(c[i], s20[i]),
                "above_50sma": _above(c[i], s50[i]),
                "above_200sma": _above(c[i], s200[i]),
                "sma_stack_10_20_50": _stack(s10[i], s20[i], s50[i]),
                "consolidation_days": base.length if base else None,
                "consolidation_high": base.high if base else None,
                "consolidation_low": base.low if base else None,
                "consolidation_depth_pct": base.depth_pct if base else None,
                "range_contraction": base.range_contraction if base else None,
                "volume_contraction": base.volume_contraction if base else None,
                "higher_low_ratio": base.higher_low_ratio if base else None,
                "base_quality": base.quality if base else None,
                "base_qualifies": int(base.qualifies) if base else 0,
                "breakout_distance_pct": (float((level / c[i] - 1) * 100.0)
                                          if base and c[i] > 0 else None),
                "breakout_today": int(bool(trig[i])),
                "breakout_volume_ratio": _at(brk_vol_ratio, i),
                "close_position": _at(close_pos, i),
                "pct_from_52w_high": _at(from_hi, i),
                "relative_volume": _at(ind["relative_volume"], i),
                "adr20": _at(ind["adr20"], i), "atr14": _at(ind["atr14"], i),
                "dollar_volume_20d": _at(ind["dollar_volume_20d"], i),
                # cross-sectional fields are filled by compute_universe
                "market_relative_strength": None,
                "relative_strength_pct": None,
                "sector_relative_strength": None,
            })

        self.store.upsert_indicators(symbol, ind_rows, timeframe,
                                     self.settings.feature_version)
        self.store.upsert_strategy_features(symbol, feat_rows, timeframe,
                                            self.settings.feature_version,
                                            config_hash(self.spec))
        return {"symbol": symbol.upper(), "rows": len(feat_rows),
                "indicator_rows": len(ind_rows), "bars": n,
                "first": series.ts[start].date().isoformat() if n else None,
                "last": series.ts[-1].date().isoformat() if n else None}

    # -- universe ----------------------------------------------------------
    def compute_universe(self, symbols: Iterable[str] | None = None,
                         timeframe: str = "1d", lookback_bars: int | None = None,
                         benchmark: str = "SPY",
                         progress: bool = False) -> dict[str, Any]:
        t0 = time.time()
        syms = [s.upper() for s in (symbols or self.store.symbol_list())]
        done, skipped = [], []
        for sym in syms:
            r = self.compute_symbol(sym, timeframe, lookback_bars)
            (done if r["rows"] else skipped).append(r)
        cross = self.fill_cross_sectional(syms, timeframe, benchmark)
        self.store.log("info", "features",
                       f"computed features for {len(done)}/{len(syms)} symbols in "
                       f"{time.time() - t0:.1f}s (feature_version="
                       f"{self.settings.feature_version})")
        return {"symbols": len(syms), "computed": len(done), "skipped": len(skipped),
                "rows": sum(r["rows"] for r in done), "cross_sectional": cross,
                "feature_version": self.settings.feature_version,
                "config_hash": config_hash(self.spec),
                "seconds": round(time.time() - t0, 2),
                "skipped_detail": skipped[:20]}

    def fill_cross_sectional(self, symbols: Sequence[str], timeframe: str = "1d",
                             benchmark: str = "SPY",
                             lookback: int | None = None) -> dict[str, Any]:
        """Relative strength is a comparison, so it needs one pass over all symbols.

        Percentiles are computed per date against the symbols that had data on
        that date -- never against the present-day list, which would leak
        survivorship into a historical feature.
        """
        lb = lookback or self.spec.setup.rs_lookback
        rows = self.store.query(
            "SELECT symbol_id, ts, prior_60d_return, close FROM strategy_features "
            "WHERE timeframe=?", (timeframe,))
        if not rows:
            return {"updated": 0, "reason": "no feature rows yet"}

        bench = self.store.bars(benchmark, timeframe)
        bench_ret: dict[int, float] = {}
        if len(bench) > lb:
            r = pct_change_n(bench.close, lb)
            bench_ret = {_epoch(bench.ts[i]): float(r[i])
                         for i in range(len(bench)) if np.isfinite(r[i])}

        sector_of = {self.store.symbol_id(s, create=False): sec
                     for s, sec in self.store.sector_map().items()}

        by_ts: dict[int, list[dict[str, Any]]] = {}
        for r in rows:
            if r["prior_60d_return"] is None:
                continue
            by_ts.setdefault(r["ts"], []).append(r)

        updates: list[tuple] = []
        for ts, group in by_ts.items():
            vals = np.array([g["prior_60d_return"] for g in group], float)
            order = np.argsort(np.argsort(vals))
            pct = order / max(len(vals) - 1, 1) * 100.0
            b = bench_ret.get(ts)
            sector_means: dict[str, float] = {}
            for sec in {sector_of.get(g["symbol_id"]) for g in group}:
                if sec is None:
                    continue
                members = [g["prior_60d_return"] for g in group
                           if sector_of.get(g["symbol_id"]) == sec]
                if members:
                    sector_means[sec] = float(np.median(members))
            for g, p in zip(group, pct):
                sec = sector_of.get(g["symbol_id"])
                updates.append((
                    float(g["prior_60d_return"] - b) if b is not None else None,
                    float(p),
                    sector_means.get(sec) if sec else None,
                    g["symbol_id"], ts, timeframe))
        self.store.executemany(
            "UPDATE strategy_features SET market_relative_strength=?, "
            "relative_strength_pct=?, sector_relative_strength=? "
            "WHERE symbol_id=? AND ts=? AND timeframe=?", updates)
        return {"updated": len(updates), "dates": len(by_ts),
                "benchmark": benchmark if bench_ret else None,
                "benchmark_note": (None if bench_ret else
                                   f"{benchmark} has no stored bars; market-relative "
                                   "strength left NULL rather than guessed")}

    def refresh_latest(self, symbols: Iterable[str] | None = None,
                       timeframe: str = "1d", bars: int = 5) -> dict[str, Any]:
        """Cheap incremental pass: recompute only the most recent bars.

        Indicators have warm-up, so the engine still reads full history; it just
        writes the tail. That keeps a nightly refresh proportional to new data.
        """
        return self.compute_universe(symbols, timeframe, lookback_bars=bars)


def _ema_of(x: np.ndarray, n: int) -> np.ndarray:
    """EMA over an array that already contains NaN warm-up."""
    out = np.full(x.shape, np.nan)
    valid = np.flatnonzero(np.isfinite(x))
    if len(valid) < n:
        return out
    seg = ema(x[valid[0]:], n)
    out[valid[0]:] = seg
    return out


def _rolling_vwap(h, l, c, v, n: int) -> np.ndarray:
    tp = (h + l + c) / 3.0
    num = sma(tp * v, n)
    den = sma(v, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(den > 0, num / den, np.nan)


def _at(arr: np.ndarray, i: int) -> float | None:
    v = float(arr[i])
    return v if np.isfinite(v) else None


def _pct_of(price: float, ref: float) -> float | None:
    if not np.isfinite(ref) or ref <= 0:
        return None
    return float((price / ref - 1.0) * 100.0)


def _above(price: float, ref: float) -> int | None:
    if not np.isfinite(ref):
        return None
    return int(price > ref)


def _stack(a: float, b: float, c: float) -> int | None:
    if not (np.isfinite(a) and np.isfinite(b) and np.isfinite(c)):
        return None
    return int(a > b > c)


FEATURES = FeatureEngine()
