"""One definition of "does this bar qualify", shared by every engine.

The backtester, the daily scanner, the trade analyzer and the historical-analog
search must agree on what a setup *is*, or their statistics describe different
things while appearing comparable. So the qualification logic lives here, once,
and all four call it.

Point-in-time: ``SymbolContext`` precomputes arrays that satisfy "element i uses
only bars <= i", and ``Evaluator.evaluate(ctx, i)`` reads nothing past ``i``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np

from ..data.types import PriceSeries
from ..indicators.core import (adr_pct, atr, dollar_volume, ema, relative_volume,
                               slope_r2, sma)
from ..indicators.structure import (BreakoutParams, ConsolidationParams, PriorMoveParams,
                                    find_prior_move, scan_breakouts)
from .spec import EntrySpec, SetupSpec, StrategySpec


@dataclasses.dataclass
class Verdict:
    qualifies: bool
    reasons_failed: list[str]
    snapshot: dict[str, Any]
    #: Stable, value-free labels for the same failures, so rejection tallies
    #: aggregate ("prior move") instead of exploding into one bucket per number.
    fail_categories: list[str] = dataclasses.field(default_factory=list)

    @property
    def primary_failure(self) -> str:
        return self.fail_categories[0] if self.fail_categories else (
            self.reasons_failed[0] if self.reasons_failed else "unspecified")


class SymbolContext:
    """All precomputed arrays for one symbol under one strategy specification."""

    __slots__ = ("symbol", "series", "sma", "adr", "rvol", "dollar_vol", "r2", "atr14",
                 "cons", "breakouts", "trail_ma", "date_index", "spec")

    def __init__(self, series: PriceSeries, spec: StrategySpec) -> None:
        c, h, l, v = series.close, series.high, series.low, series.volume
        self.symbol = series.symbol
        self.series = series
        self.spec = spec
        lengths = {10, 20, 50, 200, spec.setup.cons_ma_len,
                   *(spec.setup.require_close_above_smas or []),
                   *(spec.setup.require_sma_stack or [])}
        self.sma: dict[int, np.ndarray] = {n: sma(c, n) for n in sorted(lengths) if n > 0}
        self.adr = adr_pct(h, l, 20)
        self.rvol = relative_volume(v, 50)
        self.dollar_vol = dollar_volume(c, v, 20)
        self.r2 = slope_r2(c, 20)
        self.atr14 = atr(h, l, c, 14)
        cp = consolidation_params(spec.setup)
        bp = breakout_params(spec.entry)
        self.breakouts, self.cons = scan_breakouts(
            h, l, c, v, cp, bp, self.sma.get(spec.setup.cons_ma_len), self.rvol)
        t = spec.management.trail
        if t.type == "sma":
            self.trail_ma = sma(c, t.length)
        elif t.type == "ema":
            self.trail_ma = ema(c, t.length)
        else:
            self.trail_ma = np.full(len(c), np.nan)
        self.date_index = {ts.date(): i for i, ts in enumerate(series.ts)}

    def ma(self, length: int) -> np.ndarray:
        if length not in self.sma:
            self.sma[length] = sma(self.series.close, length)
        return self.sma[length]


def consolidation_params(s: SetupSpec) -> ConsolidationParams:
    return ConsolidationParams(
        min_len=s.cons_min_len, max_len=s.cons_max_len,
        max_depth_pct=s.cons_max_depth_pct, max_range_ratio=s.cons_max_range_ratio,
        max_volume_ratio=s.cons_max_volume_ratio,
        require_ma_support=s.cons_require_ma_support, ma_len=s.cons_ma_len,
        min_close_above_ma_frac=s.cons_min_close_above_ma_frac)


def breakout_params(e: EntrySpec) -> BreakoutParams:
    return BreakoutParams(min_rvol=e.breakout_min_rvol,
                          require_gt_prev_volume=e.breakout_require_gt_prev_volume,
                          min_close_position=e.breakout_min_close_position,
                          buffer_pct=e.breakout_buffer_pct)


def prior_move_params(s: SetupSpec) -> PriorMoveParams:
    return PriorMoveParams(lookback=s.prior_move_lookback, min_pct=s.prior_move_min_pct,
                           max_bars=s.prior_move_max_bars, min_bars=s.prior_move_min_bars,
                           max_single_bar_share=s.prior_move_max_single_bar_share)


class Evaluator:
    """Applies a StrategySpec's setup/universe rules to one bar."""

    def __init__(self, spec: StrategySpec) -> None:
        self.spec = spec

    def evaluate(self, ctx: SymbolContext, i: int, *, require_breakout: bool = True,
                 rs_percentile: float | None = None,
                 sector_percentile: float | None = None,
                 collect_all_reasons: bool = False) -> Verdict:
        s, u = self.spec.setup, self.spec.universe
        ser = ctx.series
        fails: list[str] = []
        cats: list[str] = []
        snap: dict[str, Any] = {"symbol": ctx.symbol, "date": ser.ts[i].date().isoformat()}

        def fail(msg: str, category: str = "") -> bool:
            fails.append(msg)
            cats.append(category or msg)
            return not collect_all_reasons

        if require_breakout and not ctx.breakouts[i]:
            if fail("no qualifying breakout on this bar", "no breakout"):
                return Verdict(False, fails, snap, cats)

        px = float(ser.close[i])
        snap["close"] = round(px, 4)
        if px < u.min_price and fail(f"price {px:.2f} below minimum {u.min_price}", "price band"):
            return Verdict(False, fails, snap, cats)
        if u.max_price and px > u.max_price and fail(f"price above maximum {u.max_price}", "price band"):
            return Verdict(False, fails, snap, cats)

        dv = float(ctx.dollar_vol[i]) if np.isfinite(ctx.dollar_vol[i]) else 0.0
        snap["dollar_volume_20d"] = round(dv, 0)
        if dv < u.min_dollar_volume and fail(
                f"20-day dollar volume ${dv:,.0f} below ${u.min_dollar_volume:,.0f}",
                "liquidity"):
            return Verdict(False, fails, snap, cats)

        adr = float(ctx.adr[i]) if np.isfinite(ctx.adr[i]) else float("nan")
        snap["adr_pct"] = round(adr, 2) if np.isfinite(adr) else None
        if u.min_adr_pct and (not np.isfinite(adr) or adr < u.min_adr_pct) and fail(
                f"ADR {adr:.2f}% below minimum {u.min_adr_pct}%", "ADR floor"):
            return Verdict(False, fails, snap, cats)
        if u.max_adr_pct and np.isfinite(adr) and adr > u.max_adr_pct and fail(
                f"ADR {adr:.2f}% above maximum {u.max_adr_pct}%", "ADR ceiling"):
            return Verdict(False, fails, snap, cats)

        for length in s.require_close_above_smas:
            m = ctx.ma(length)
            if not (np.isfinite(m[i]) and px > m[i]):
                msg = (f"close {px:.2f} not above the {length} SMA ({m[i]:.2f})"
                       if np.isfinite(m[i]) else f"{length} SMA not yet defined (warm-up)")
                if fail(msg, f"close below {length} SMA"):
                    return Verdict(False, fails, snap, cats)

        if s.require_sma_stack:
            vals = [float(ctx.ma(n)[i]) for n in s.require_sma_stack]
            snap["sma_stack"] = {str(n): (round(v, 4) if np.isfinite(v) else None)
                                 for n, v in zip(s.require_sma_stack, vals)}
            if any(not np.isfinite(v) for v in vals):
                if fail("moving-average stack not yet defined (warm-up)", "SMA stack warm-up"):
                    return Verdict(False, fails, snap, cats)
            elif any(a <= b for a, b in zip(vals, vals[1:])):
                if fail("moving averages not stacked "
                        + " > ".join(str(n) for n in s.require_sma_stack),
                        "SMA stack not aligned"):
                    return Verdict(False, fails, snap, cats)

        pm = find_prior_move(ser.high, ser.low, i, prior_move_params(s))
        snap["prior_move_pct"] = round(pm.pct, 2)
        snap["prior_move_bars"] = pm.bars
        snap["prior_move_largest_bar_share"] = round(pm.largest_bar_share, 3)
        if not pm.qualifies and fail(
                f"prior move rejected: {pm.rejected_because}", "prior move"):
            return Verdict(False, fails, snap, cats)

        base = ctx.cons.at(i - 1 if require_breakout else i)
        if base is None:
            if fail("no consolidation base found", "no base"):
                return Verdict(False, fails, snap, cats)
        else:
            snap.update({
                "base_length": base.length, "base_high": round(base.high, 4),
                "base_low": round(base.low, 4), "base_depth_pct": round(base.depth_pct, 2),
                "base_range_contraction": round(base.range_contraction, 3),
                "base_volume_contraction": round(base.volume_contraction, 3),
                "base_higher_low_ratio": round(base.higher_low_ratio, 3),
                "base_quality": round(base.quality, 3),
                "base_qualifies": base.qualifies,
                "breakout_level": round(base.high, 4),
            })
            if base.quality < s.cons_min_quality and fail(
                    f"base quality {base.quality:.2f} below minimum {s.cons_min_quality}",
                    "base quality"):
                return Verdict(False, fails, snap, cats)

        m20 = ctx.ma(20)
        if np.isfinite(m20[i]) and m20[i] > 0:
            ext = (px / m20[i] - 1) * 100.0
            snap["pct_above_sma20"] = round(ext, 2)
            if s.max_pct_above_sma20 is not None and ext > s.max_pct_above_sma20:
                if fail(f"extended: {ext:.1f}% above the 20 SMA "
                        f"(limit {s.max_pct_above_sma20}%)", "extended vs 20 SMA"):
                    return Verdict(False, fails, snap, cats)
            if s.max_adr_above_sma20 is not None and np.isfinite(adr) and adr > 0:
                in_adr = ext / adr
                snap["adr_above_sma20"] = round(in_adr, 2)
                if in_adr > s.max_adr_above_sma20 and fail(
                        f"extended: {in_adr:.1f} ADRs above the 20 SMA "
                        f"(limit {s.max_adr_above_sma20})", "extended in ADR units"):
                    return Verdict(False, fails, snap, cats)

        snap["r2_20"] = round(float(ctx.r2[i]), 3) if np.isfinite(ctx.r2[i]) else None
        if s.min_r2_20 is not None:
            if not (np.isfinite(ctx.r2[i]) and ctx.r2[i] >= s.min_r2_20):
                if fail(f"price path R^2 {snap['r2_20']} below minimum {s.min_r2_20} "
                        "(not 'orderly' by this measure)", "orderliness (R^2)"):
                    return Verdict(False, fails, snap, cats)

        if rs_percentile is not None:
            snap["rs_percentile"] = round(rs_percentile, 1)
        if s.min_rs_percentile is not None:
            if rs_percentile is None or not np.isfinite(rs_percentile):
                if fail("relative-strength percentile unavailable", "RS unavailable"):
                    return Verdict(False, fails, snap, cats)
            elif rs_percentile < s.min_rs_percentile:
                if fail(f"relative strength {rs_percentile:.0f}th percentile below "
                        f"required {s.min_rs_percentile:.0f}th", "relative strength"):
                    return Verdict(False, fails, snap, cats)

        if sector_percentile is not None:
            snap["sector_percentile"] = round(sector_percentile, 1)
        sf = self.spec.sector_filter
        if sf.enabled:
            if sector_percentile is None:
                if fail("sector ranking unavailable", "sector unavailable"):
                    return Verdict(False, fails, snap, cats)
            elif sector_percentile < sf.min_rank_percentile:
                if fail(f"sector ranks {sector_percentile:.0f}th percentile, below "
                        f"required {sf.min_rank_percentile:.0f}th", "sector strength"):
                    return Verdict(False, fails, snap, cats)

        snap["rvol"] = round(float(ctx.rvol[i]), 2) if np.isfinite(ctx.rvol[i]) else None
        snap["signal_low"] = round(float(ser.low[i]), 4)
        return Verdict(not fails, fails, snap, cats)
