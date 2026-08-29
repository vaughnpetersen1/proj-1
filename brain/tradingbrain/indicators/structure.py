"""Setup-structure detectors.

These turn the *qualitative* language of the source material -- "a big prior
move", "an orderly consolidation", "volume drying up", "a range expansion out of
that consolidation" -- into measurable objects.

Every threshold here is a **parameter**, never a constant, because the whole
point of the research engine is that no single operationalisation is assumed
correct. ``research.formalizer`` enumerates competing parameter sets and the
Hypothesis Lab tests them against each other.

All functions are point-in-time: a detector standing on bar ``i`` reads only
bars ``<= i``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np

from .core import (adr_pct, atr, distance_pct, relative_volume, rolling_max, rolling_min,
                   sma, slope_r2)

__all__ = [
    "ConsolidationParams", "Consolidation", "find_consolidation",
    "ConsolidationScan", "scan_consolidations", "scan_breakouts",
    "PriorMove", "find_prior_move", "PriorMoveParams",
    "BreakoutParams", "BreakoutSignal", "detect_breakout",
    "extension_metrics", "opening_range", "SetupFeatures", "compute_features",
]


# ---------------------------------------------------------------------------
# Prior move  ("large prior price expansion")
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class PriorMoveParams:
    """One candidate operationalisation of 'a big move up'."""
    lookback: int = 63          # bars searched for the launch point (~3 months)
    min_pct: float = 30.0       # required advance, percent
    max_bars: int = 60          # advance must complete within this many bars
    min_bars: int = 3           # the peak must be at least this far from the launch bar
    #: The source is explicit that a one-day pump does not count. Duration alone
    #: does not capture that: a flat month followed by a single +50% gap has a
    #: 40-bar "advance" that is really one bar. So the largest single bar may not
    #: account for more than this share of the total log gain.
    max_single_bar_share: float = 0.80
    label: str = "gain>=30% within 60 bars, no single bar >80% of the move"


@dataclasses.dataclass
class PriorMove:
    pct: float
    bars: int
    low_idx: int
    high_idx: int
    qualifies: bool
    #: Share of the total log advance contributed by its single largest bar.
    largest_bar_share: float = 0.0
    rejected_because: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def find_prior_move(high: np.ndarray, low: np.ndarray, i: int,
                    p: PriorMoveParams = PriorMoveParams()) -> PriorMove:
    """Largest low->high advance ending at or before bar ``i``.

    Searches every (low, high) pair inside the lookback window subject to the
    duration constraints and returns the largest qualifying advance.
    """
    start = max(0, i - p.lookback + 1)
    win_lo = low[start: i + 1]
    win_hi = high[start: i + 1]
    n = len(win_hi)
    if n < p.min_bars + 1:
        return PriorMove(0.0, 0, i, i, False, rejected_because="not enough bars")

    best = (0.0, 0, i, i)
    for b in range(n):
        base = win_lo[b]
        if not np.isfinite(base) or base <= 0:
            continue
        top = min(n, b + p.max_bars + 1)
        seg = win_hi[b:top]
        if len(seg) <= p.min_bars:
            continue
        k = int(np.argmax(seg))
        if k < p.min_bars:
            # the max is too soon; look for the best qualifying later peak
            later = seg[p.min_bars:]
            if len(later) == 0:
                continue
            k = p.min_bars + int(np.argmax(later))
        gain = float((seg[k] / base - 1.0) * 100.0)
        # Prefer the biggest advance; on a tie prefer the *tightest* one, so a
        # flat prefix does not get counted as part of the move.
        if gain > best[0] + 1e-9 or (abs(gain - best[0]) <= 1e-9 and k < best[1]):
            best = (gain, int(k), start + b, start + b + k)

    pct, bars, lo_i, hi_i = best
    share = _largest_bar_share(high, low, lo_i, hi_i)
    reason = None
    ok = True
    if pct < p.min_pct:
        ok, reason = False, f"advance {pct:.1f}% is below {p.min_pct}%"
    elif bars < p.min_bars:
        ok, reason = False, f"advance spanned {bars} bars, fewer than {p.min_bars}"
    elif share > p.max_single_bar_share:
        ok, reason = False, (f"a single bar accounts for {share:.0%} of the advance "
                             f"(limit {p.max_single_bar_share:.0%}): this is a one-day pump, "
                             "not a multi-day expansion")
    return PriorMove(pct, bars, lo_i, hi_i, ok, share, reason)


def _largest_bar_share(high: np.ndarray, low: np.ndarray, lo_i: int, hi_i: int) -> float:
    """Fraction of the advance's total log gain contributed by its biggest bar."""
    if hi_i <= lo_i:
        return 1.0
    seg_hi = high[lo_i:hi_i + 1]
    seg_lo = low[lo_i:hi_i + 1]
    if np.any(seg_hi <= 0) or np.any(seg_lo <= 0):
        return 1.0
    # bar-to-bar log change of the running high, floored at zero
    ref = np.maximum.accumulate(seg_hi)
    steps = np.diff(np.log(np.maximum(ref, 1e-12)))
    total = float(np.sum(steps))
    if total <= 1e-12:
        return 1.0
    return float(np.max(steps) / total)


# ---------------------------------------------------------------------------
# Consolidation  ("orderly pullback / tightening range / volume drying up")
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class ConsolidationParams:
    min_len: int = 5
    max_len: int = 40
    max_depth_pct: float = 35.0        # peak-to-trough retracement inside the base
    max_range_ratio: float = 1.10      # 2nd-half daily range / 1st-half (<1 = tightening)
    max_volume_ratio: float = 1.10     # 2nd-half volume / 1st-half   (<1 = drying up)
    require_ma_support: bool = True    # base must hold above the 20 SMA
    ma_len: int = 20
    min_close_above_ma_frac: float = 0.60
    label: str = "5-40 bars, depth<=35%, range and volume contracting, holds 20SMA"


@dataclasses.dataclass
class Consolidation:
    start_idx: int
    end_idx: int
    length: int
    high: float
    low: float
    depth_pct: float
    range_contraction: float      # <1.0 means the range tightened
    volume_contraction: float     # <1.0 means volume dried up
    higher_low_ratio: float       # fraction of 3-bar swing lows that stepped up
    close_above_ma_frac: float
    tightness_pct: float          # mean daily range as % of price inside the base
    quality: float                # 0..1 composite, see `_score`
    qualifies: bool

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _higher_low_ratio(low: np.ndarray) -> float:
    """Fraction of consecutive sub-block minima that stepped up.

    The base is cut into ``L // k`` blocks of ``k = max(2, L // 4)`` bars each,
    anchored at the *end* of the base so the most recent lows always count and
    any remainder is dropped from the oldest end. ``scan_consolidations``
    reproduces this exactly.
    """
    L = len(low)
    if L < 4:
        return 0.0
    k = max(2, L // 4)
    nb = L // k
    if nb < 2:
        return 0.0
    tail = low[L - nb * k:]
    mins = [float(np.min(tail[j * k:(j + 1) * k])) for j in range(nb)]
    ups = sum(1 for a, b in zip(mins, mins[1:]) if b >= a)
    return ups / (nb - 1)


def _score(depth_pct: float, range_contraction: float, volume_contraction: float,
           hl_ratio: float, close_above_ma_frac: float, p: ConsolidationParams) -> float:
    """Composite 0..1 base quality.

    Weights here are a *starting hypothesis*, not a finding. They are exposed in
    the Strategy Builder and tested by ``research.hypothesis`` against
    alternatives; nothing downstream may treat them as validated.
    """
    depth_term = max(0.0, 1.0 - depth_pct / max(p.max_depth_pct, 1e-9))
    range_term = float(np.clip(1.5 - range_contraction, 0.0, 1.0))
    vol_term = float(np.clip(1.5 - volume_contraction, 0.0, 1.0))
    return float(np.clip(
        0.30 * depth_term + 0.25 * range_term + 0.20 * vol_term
        + 0.15 * hl_ratio + 0.10 * close_above_ma_frac, 0.0, 1.0))


def find_consolidation(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                       volume: np.ndarray, i: int,
                       p: ConsolidationParams = ConsolidationParams(),
                       ma: np.ndarray | None = None) -> Consolidation | None:
    """Best consolidation *ending at bar i*, or None.

    Every candidate length in [min_len, max_len] is scored and the highest
    scoring qualifying base wins. Ties break toward the longer base.
    """
    if i < p.min_len - 1:
        return None
    if ma is None and p.require_ma_support:
        ma = sma(close, p.ma_len)

    best: Consolidation | None = None
    for L in range(p.min_len, min(p.max_len, i + 1) + 1):
        s = i - L + 1
        if s < 0:
            break
        h = high[s:i + 1]
        lo = low[s:i + 1]
        c = close[s:i + 1]
        v = volume[s:i + 1]
        if not (np.all(np.isfinite(h)) and np.all(np.isfinite(lo))):
            continue
        base_high = float(np.max(h))
        base_low = float(np.min(lo))
        if base_high <= 0:
            continue
        depth = (base_high - base_low) / base_high * 100.0
        if depth > p.max_depth_pct:
            continue

        half = max(1, L // 2)
        rng = (h - lo) / np.maximum(c, 1e-9)
        r1, r2 = float(np.mean(rng[:half])), float(np.mean(rng[half:]))
        range_contraction = r2 / r1 if r1 > 0 else np.inf
        v1, v2 = float(np.mean(v[:half])), float(np.mean(v[half:]))
        volume_contraction = v2 / v1 if v1 > 0 else np.inf

        hl = _higher_low_ratio(lo)
        if ma is not None:
            seg_ma = ma[s:i + 1]
            valid = np.isfinite(seg_ma)
            above = float(np.mean(c[valid] > seg_ma[valid])) if valid.any() else 0.0
        else:
            above = 1.0

        qualifies = (range_contraction <= p.max_range_ratio
                     and volume_contraction <= p.max_volume_ratio
                     and (not p.require_ma_support or above >= p.min_close_above_ma_frac))
        cand = Consolidation(
            start_idx=s, end_idx=i, length=L, high=base_high, low=base_low,
            depth_pct=depth, range_contraction=float(range_contraction),
            volume_contraction=float(volume_contraction), higher_low_ratio=hl,
            close_above_ma_frac=above, tightness_pct=float(np.mean(rng) * 100.0),
            quality=_score(depth, float(range_contraction), float(volume_contraction),
                           hl, above, p),
            qualifies=bool(qualifies),
        )
        if best is None:
            best = cand
        elif (cand.qualifies, round(cand.quality, 4), cand.length) > \
             (best.qualifies, round(best.quality, 4), best.length):
            best = cand
    return best


# ---------------------------------------------------------------------------
# Breakout  ("range expansion out of the consolidation, on volume")
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class BreakoutParams:
    min_rvol: float = 1.5              # breakout volume vs 50-bar average
    require_gt_prev_volume: bool = True
    min_close_position: float = 0.50   # (close-low)/(high-low) on the breakout bar
    buffer_pct: float = 0.0            # how far above the base high counts as a break
    label: str = "close>base high, rvol>=1.5, closes in upper half of the bar"


@dataclasses.dataclass
class BreakoutSignal:
    triggered: bool
    base: Consolidation | None
    breakout_level: float
    close_position: float
    rvol: float
    volume_expansion: bool
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["base"] = self.base.to_dict() if self.base else None
        return d


def detect_breakout(high, low, close, volume, i: int,
                    cp: ConsolidationParams = ConsolidationParams(),
                    bp: BreakoutParams = BreakoutParams(),
                    ma: np.ndarray | None = None,
                    rvol: np.ndarray | None = None) -> BreakoutSignal:
    """Is bar ``i`` a breakout from a base that ended at bar ``i-1``?"""
    reasons: list[str] = []
    base = find_consolidation(high, low, close, volume, i - 1, cp, ma)
    if base is None:
        return BreakoutSignal(False, None, float("nan"), float("nan"), float("nan"),
                              False, ["no consolidation found ending on the prior bar"])
    level = base.high * (1.0 + bp.buffer_pct / 100.0)
    rng = high[i] - low[i]
    pos = float((close[i] - low[i]) / rng) if rng > 0 else 1.0
    rv = float(rvol[i]) if rvol is not None and np.isfinite(rvol[i]) else float("nan")
    vol_up = bool(volume[i] > volume[i - 1]) if i > 0 else False

    if not base.qualifies:
        reasons.append("base did not meet the consolidation criteria")
    if not close[i] > level:
        reasons.append(f"close {close[i]:.2f} did not exceed base high {level:.2f}")
    if np.isfinite(rv) and rv < bp.min_rvol:
        reasons.append(f"relative volume {rv:.2f} below required {bp.min_rvol}")
    if bp.require_gt_prev_volume and not vol_up:
        reasons.append("breakout volume did not exceed the prior bar")
    if pos < bp.min_close_position:
        reasons.append(f"closed at {pos:.0%} of the bar's range, below "
                       f"{bp.min_close_position:.0%} (did not close near the high)")

    return BreakoutSignal(not reasons, base, float(level), pos, rv, vol_up, reasons)


# ---------------------------------------------------------------------------
# Extension  ("avoid extended stocks")
# ---------------------------------------------------------------------------

def extension_metrics(close, high, low, i: int, ma_len: int = 20,
                      atr_len: int = 14) -> dict[str, float]:
    """How far above its own trend the stock has travelled.

    Three competing measures are reported rather than one, because the sources
    describe 'extended' qualitatively and each measure disagrees with the others
    in ordinary cases.
    """
    m = sma(close, ma_len)
    a = atr(high, low, close, atr_len)
    ad = adr_pct(high, low, 20)
    px = float(close[i])
    out = {
        "pct_above_ma": float(distance_pct(close, m)[i]),
        "atr_above_ma": float((px - m[i]) / a[i]) if np.isfinite(a[i]) and a[i] > 0 else float("nan"),
        "adr_above_ma": float((px / m[i] - 1) * 100.0 / ad[i])
                        if np.isfinite(ad[i]) and ad[i] > 0 else float("nan"),
        "pct_from_20d_high": float((px / np.max(high[max(0, i - 19):i + 1]) - 1) * 100.0),
    }
    return out


# ---------------------------------------------------------------------------
# Opening range  ("1-minute / 5-minute / opening-range confirmation")
# ---------------------------------------------------------------------------

def opening_range(ts, high, low, day, minutes: int, bar_minutes: int) -> dict[str, Any]:
    """High/low of the first ``minutes`` of the session on ``day``."""
    n_bars = max(1, minutes // max(bar_minutes, 1))
    idx = [k for k, t in enumerate(ts) if t.date() == day]
    if not idx:
        return {"available": False, "reason": f"no intraday bars for {day}"}
    sel = idx[:n_bars]
    return {
        "available": True, "minutes": minutes, "bars_used": len(sel),
        "high": float(np.max(high[sel])), "low": float(np.min(low[sel])),
        "first_bar": ts[sel[0]].isoformat(), "last_bar": ts[sel[-1]].isoformat(),
    }


# ---------------------------------------------------------------------------
# Bundled feature computation (used by scanner, analyzer and backtester)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class SetupFeatures:
    """All per-bar arrays a strategy might reference, computed once per symbol."""
    sma10: np.ndarray
    sma20: np.ndarray
    sma50: np.ndarray
    sma200: np.ndarray
    atr14: np.ndarray
    adr20: np.ndarray
    rvol50: np.ndarray
    dollar_vol20: np.ndarray
    r2_20: np.ndarray
    ret_1m: np.ndarray
    ret_3m: np.ndarray
    ret_6m: np.ndarray
    ret_12m: np.ndarray
    pct_from_52w_high: np.ndarray
    dist_sma10: np.ndarray
    dist_sma20: np.ndarray

    def to_row(self, i: int) -> dict[str, float]:
        return {k: (float(getattr(self, k)[i]) if i < len(getattr(self, k)) else float("nan"))
                for k in self.__dataclass_fields__}


def compute_features(series) -> SetupFeatures:
    from .core import dollar_volume, pct_change_n, rolling_max
    c, h, l, v = series.close, series.high, series.low, series.volume
    hi52 = rolling_max(h, min(252, max(2, len(h))))
    with np.errstate(divide="ignore", invalid="ignore"):
        from_hi = np.where(hi52 > 0, (c / hi52 - 1.0) * 100.0, np.nan)
    s10, s20 = sma(c, 10), sma(c, 20)
    return SetupFeatures(
        sma10=s10, sma20=s20, sma50=sma(c, 50), sma200=sma(c, 200),
        atr14=atr(h, l, c, 14), adr20=adr_pct(h, l, 20),
        rvol50=relative_volume(v, 50), dollar_vol20=dollar_volume(c, v, 20),
        r2_20=slope_r2(c, 20),
        ret_1m=pct_change_n(c, 21), ret_3m=pct_change_n(c, 63),
        ret_6m=pct_change_n(c, 126), ret_12m=pct_change_n(c, 252),
        pct_from_52w_high=from_hi,
        dist_sma10=distance_pct(c, s10), dist_sma20=distance_pct(c, s20),
    )


# ---------------------------------------------------------------------------
# Vectorised scan (what the backtester and scanner actually use)
# ---------------------------------------------------------------------------

def _shift(a: np.ndarray, k: int) -> np.ndarray:
    """out[i] = a[i-k], nan-padded. k >= 0."""
    out = np.full(a.shape, np.nan)
    if k == 0:
        return a.copy()
    if k < len(a):
        out[k:] = a[:-k]
    return out


def _cumsum0(a: np.ndarray) -> np.ndarray:
    return np.concatenate([[0.0], np.cumsum(np.nan_to_num(a, nan=0.0))])


@dataclasses.dataclass
class ConsolidationScan:
    """Best qualifying base ending at every bar, computed in one pass.

    Semantically identical to calling :func:`find_consolidation` at each bar --
    ``tests/test_structure.py`` asserts they agree -- but ~200x faster, which is
    what makes universe-wide backtests and scans practical.
    """

    params: ConsolidationParams
    best_len: np.ndarray
    high: np.ndarray
    low: np.ndarray
    depth_pct: np.ndarray
    range_contraction: np.ndarray
    volume_contraction: np.ndarray
    higher_low_ratio: np.ndarray
    close_above_ma_frac: np.ndarray
    tightness_pct: np.ndarray
    quality: np.ndarray
    qualifies: np.ndarray

    def at(self, i: int) -> Consolidation | None:
        if i < 0 or i >= len(self.best_len) or self.best_len[i] <= 0:
            return None
        L = int(self.best_len[i])
        return Consolidation(
            start_idx=i - L + 1, end_idx=i, length=L,
            high=float(self.high[i]), low=float(self.low[i]),
            depth_pct=float(self.depth_pct[i]),
            range_contraction=float(self.range_contraction[i]),
            volume_contraction=float(self.volume_contraction[i]),
            higher_low_ratio=float(self.higher_low_ratio[i]),
            close_above_ma_frac=float(self.close_above_ma_frac[i]),
            tightness_pct=float(self.tightness_pct[i]),
            quality=float(self.quality[i]),
            qualifies=bool(self.qualifies[i]),
        )


def scan_consolidations(high, low, close, volume,
                        p: ConsolidationParams = ConsolidationParams(),
                        ma: np.ndarray | None = None) -> ConsolidationScan:
    high = np.asarray(high, float); low = np.asarray(low, float)
    close = np.asarray(close, float); volume = np.asarray(volume, float)
    n = len(close)
    if ma is None and p.require_ma_support:
        ma = sma(close, p.ma_len)

    rng = (high - low) / np.maximum(close, 1e-9)
    cs_rng = _cumsum0(rng)
    cs_vol = _cumsum0(volume)
    if ma is not None:
        above = (close > ma).astype(float)
        valid_ma = np.isfinite(ma).astype(float)
        above = np.where(np.isfinite(ma), above, 0.0)
        cs_above = _cumsum0(above)
        cs_valid = _cumsum0(valid_ma)

    nan = np.full(n, np.nan)
    best = {k: nan.copy() for k in
            ("high", "low", "depth", "rc", "vc", "hl", "am", "tight", "q")}
    best_len = np.zeros(n)
    best_qual = np.zeros(n, dtype=bool)
    best_key = np.full(n, -np.inf)

    idx = np.arange(n)
    for L in range(p.min_len, min(p.max_len, max(n - 1, 1)) + 1):
        if L > n:
            break
        bh = rolling_max(high, L)
        bl = rolling_min(low, L)
        with np.errstate(divide="ignore", invalid="ignore"):
            depth = np.where(bh > 0, (bh - bl) / bh * 100.0, np.nan)

        s = idx - L + 1                       # window start index
        ok = s >= 0
        half = max(1, L // 2)
        sl = np.clip(s, 0, n)
        r_first = (cs_rng[np.clip(sl + half, 0, n)] - cs_rng[sl]) / half
        r_second = (cs_rng[idx + 1] - cs_rng[np.clip(sl + half, 0, n)]) / max(L - half, 1)
        v_first = (cs_vol[np.clip(sl + half, 0, n)] - cs_vol[sl]) / half
        v_second = (cs_vol[idx + 1] - cs_vol[np.clip(sl + half, 0, n)]) / max(L - half, 1)
        with np.errstate(divide="ignore", invalid="ignore"):
            rc = np.where(r_first > 0, r_second / r_first, np.inf)
            vc = np.where(v_first > 0, v_second / v_first, np.inf)
            tight = (cs_rng[idx + 1] - cs_rng[sl]) / L * 100.0

        # higher lows: split the base into blocks of k bars, compare block minima
        k = max(2, L // 4)
        nblocks = L // k
        if nblocks >= 2:
            rm = rolling_min(low, k)
            blocks = [_shift(rm, (nblocks - j - 1) * k) for j in range(nblocks)]
            ups = np.zeros(n)
            for a, b in zip(blocks, blocks[1:]):
                ups += (b >= a).astype(float)
            hl = ups / (nblocks - 1)
        else:
            hl = np.zeros(n)

        if ma is not None:
            cnt_valid = cs_valid[idx + 1] - cs_valid[sl]
            with np.errstate(divide="ignore", invalid="ignore"):
                am = np.where(cnt_valid > 0, (cs_above[idx + 1] - cs_above[sl]) / cnt_valid, 0.0)
        else:
            am = np.ones(n)

        depth_term = np.clip(1.0 - depth / max(p.max_depth_pct, 1e-9), 0.0, None)
        q = np.clip(0.30 * depth_term + 0.25 * np.clip(1.5 - rc, 0.0, 1.0)
                    + 0.20 * np.clip(1.5 - vc, 0.0, 1.0)
                    + 0.15 * hl + 0.10 * am, 0.0, 1.0)

        feasible = ok & np.isfinite(depth) & (depth <= p.max_depth_pct)
        qual = (feasible & (rc <= p.max_range_ratio) & (vc <= p.max_volume_ratio)
                & ((am >= p.min_close_above_ma_frac) if p.require_ma_support
                   else np.ones(n, dtype=bool)))
        # rank: qualifying first, then quality, then longer base
        # rank exactly as find_consolidation does: qualifying first, then quality
        # rounded to 4dp, then the longer base.
        key = np.where(feasible,
                       qual.astype(float) * 1e12 + np.round(np.nan_to_num(q), 4) * 1e6 + L,
                       -np.inf)
        take = feasible & (key >= best_key)
        best_key = np.where(take, key, best_key)
        best_len = np.where(take, L, best_len)
        best_qual = np.where(take, qual, best_qual)
        for name, arr in (("high", bh), ("low", bl), ("depth", depth), ("rc", rc),
                          ("vc", vc), ("hl", hl), ("am", am), ("tight", tight), ("q", q)):
            best[name] = np.where(take, arr, best[name])

    return ConsolidationScan(
        params=p, best_len=best_len, high=best["high"], low=best["low"],
        depth_pct=best["depth"], range_contraction=best["rc"],
        volume_contraction=best["vc"], higher_low_ratio=best["hl"],
        close_above_ma_frac=best["am"], tightness_pct=best["tight"],
        quality=best["q"], qualifies=best_qual.astype(bool),
    )


def scan_breakouts(high, low, close, volume,
                   cp: ConsolidationParams = ConsolidationParams(),
                   bp: BreakoutParams = BreakoutParams(),
                   ma: np.ndarray | None = None,
                   rvol: np.ndarray | None = None,
                   scan: "ConsolidationScan | None" = None) -> tuple[np.ndarray, ConsolidationScan]:
    """Boolean array: does bar i break out of the base that ended at bar i-1?"""
    high = np.asarray(high, float); low = np.asarray(low, float)
    close = np.asarray(close, float); volume = np.asarray(volume, float)
    if scan is None:
        scan = scan_consolidations(high, low, close, volume, cp, ma)
    if rvol is None:
        rvol = relative_volume(volume, 50)
    n = len(close)
    prev_level = _shift(scan.high, 1) * (1.0 + bp.buffer_pct / 100.0)
    prev_qual = np.concatenate([[False], scan.qualifies[:-1]])
    rngb = high - low
    with np.errstate(divide="ignore", invalid="ignore"):
        pos = np.where(rngb > 0, (close - low) / rngb, 1.0)
    vol_up = np.concatenate([[False], volume[1:] > volume[:-1]])
    rv_ok = ~np.isfinite(rvol) | (rvol >= bp.min_rvol)
    trig = (prev_qual & np.isfinite(prev_level) & (close > prev_level)
            & (pos >= bp.min_close_position) & rv_ok
            & (vol_up if bp.require_gt_prev_volume else np.ones(n, dtype=bool)))
    return trig.astype(bool), scan
