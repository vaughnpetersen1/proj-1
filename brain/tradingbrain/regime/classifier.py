"""Market regime classification.

The source material says: do not take breakouts when the index 10 MA is below
the 20 MA. This module does NOT treat that as settled. It computes the inputs,
classifies the environment, and exposes the classification as a *filter that can
be switched off*, so ``backtest`` can measure the filter's contribution
(with-filter vs without-filter) instead of assuming it.

Point-in-time by construction: ``classify_market(..., i)`` reads only bars <= i.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

import numpy as np

from ..data.providers.registry import HUB, DataHub
from ..indicators.core import rolling_max, sma, stdev
from ..provenance import Claim, DataOrigin, EvidenceClass


@dataclasses.dataclass(frozen=True)
class RegimeParams:
    fast: int = 10
    slow: int = 20
    medium: int = 50
    long: int = 200
    drawdown_lookback: int = 252
    vol_window: int = 20
    vol_percentile_lookback: int = 504
    correction_drawdown_pct: float = 10.0
    bear_drawdown_pct: float = 20.0
    chop_slope_threshold: float = 0.0005      # |20-SMA slope| below this = sideways


@dataclasses.dataclass
class RegimeSnapshot:
    symbol: str
    date: str
    close: float
    sma_fast: float
    sma_slow: float
    sma_medium: float
    sma_long: float
    fast_above_slow: bool
    close_above_long: bool
    drawdown_pct: float
    slope_20: float
    realized_vol_annual_pct: float
    vol_percentile: float
    label: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class MarketRegime:
    as_of: str
    label: str                     # BULL | BULL_PULLBACK | SIDEWAYS | CORRECTION | BEAR
    volatility_label: str          # LOW | NORMAL | HIGH
    strength: float                # 0..100 composite, see `_strength`
    indexes: dict[str, RegimeSnapshot]
    breadth: dict[str, Any]
    filter_pass: bool              # does the SAR-style index MA filter pass right now?
    filter_detail: str
    data_origin: str
    data_provider: str
    components: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["indexes"] = {k: v.to_dict() if hasattr(v, "to_dict") else v
                        for k, v in self.indexes.items()}
        return d

    def claim(self) -> Claim:
        return Claim(
            statement=(f"Market regime classified {self.label} "
                       f"({self.volatility_label} volatility), strength {self.strength:.0f}/100."),
            evidence=EvidenceClass.LIVE_MARKET_OBSERVATION,
            value=self.label,
            period=self.as_of,
            data_source=self.data_provider,
            data_origin=DataOrigin(self.data_origin),
            methodology=("Rule-based classification from index moving-average structure, "
                         "drawdown from the trailing 252-bar high, 20-bar realised "
                         "volatility and its percentile, plus breadth when a universe is "
                         "available. Thresholds are configurable in RegimeParams and are "
                         "not validated as optimal."),
            caveats=["A regime label is a description of the recent past, not a forecast."],
        )


def _snapshot(symbol: str, series, i: int, p: RegimeParams) -> RegimeSnapshot:
    c = series.close
    f, s, m, l = (sma(c, p.fast), sma(c, p.slow), sma(c, p.medium), sma(c, p.long))
    hi = rolling_max(c, min(p.drawdown_lookback, max(2, i + 1)))
    ret = np.diff(np.log(np.maximum(c, 1e-9)))
    vol = np.concatenate([[np.nan], stdev(ret, p.vol_window)]) * np.sqrt(252) * 100.0
    lo = max(0, i - p.vol_percentile_lookback + 1)
    hist = vol[lo:i + 1]
    hist = hist[np.isfinite(hist)]
    pct = float(np.mean(hist <= vol[i]) * 100.0) if len(hist) and np.isfinite(vol[i]) else float("nan")
    slope = float((s[i] / s[i - 5] - 1.0)) if i >= 5 and np.isfinite(s[i - 5]) and s[i - 5] else float("nan")
    dd = float((c[i] / hi[i] - 1.0) * 100.0) if np.isfinite(hi[i]) and hi[i] > 0 else float("nan")

    fa = bool(np.isfinite(f[i]) and np.isfinite(s[i]) and f[i] > s[i])
    cl = bool(np.isfinite(l[i]) and c[i] > l[i])

    if dd <= -p.bear_drawdown_pct and not fa:
        label = "BEAR"
    elif dd <= -p.correction_drawdown_pct:
        label = "CORRECTION"
    elif fa and cl and dd > -5.0:
        label = "BULL"
    elif cl and not fa:
        label = "BULL_PULLBACK"
    elif abs(slope) < p.chop_slope_threshold if np.isfinite(slope) else False:
        label = "SIDEWAYS"
    else:
        label = "SIDEWAYS"

    return RegimeSnapshot(
        symbol=symbol, date=series.ts[i].date().isoformat(), close=float(c[i]),
        sma_fast=float(f[i]), sma_slow=float(s[i]), sma_medium=float(m[i]),
        sma_long=float(l[i]), fast_above_slow=fa, close_above_long=cl,
        drawdown_pct=dd, slope_20=slope,
        realized_vol_annual_pct=float(vol[i]) if np.isfinite(vol[i]) else float("nan"),
        vol_percentile=pct, label=label)


_ORDER = {"BEAR": 0, "CORRECTION": 1, "SIDEWAYS": 2, "BULL_PULLBACK": 3, "BULL": 4}


def _strength(snaps: dict[str, RegimeSnapshot], breadth: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """0-100 composite. Weights are a documented starting point, not a result."""
    comp: dict[str, float] = {}
    trend = np.mean([_ORDER[s.label] / 4.0 for s in snaps.values()]) if snaps else 0.5
    comp["trend"] = float(trend * 100)
    ma = np.mean([1.0 if s.fast_above_slow else 0.0 for s in snaps.values()]) if snaps else 0.5
    comp["ma_structure"] = float(ma * 100)
    dds = [s.drawdown_pct for s in snaps.values() if np.isfinite(s.drawdown_pct)]
    dd = float(np.clip(1.0 + np.mean(dds) / 20.0, 0, 1)) if dds else 0.5
    comp["drawdown"] = dd * 100
    vp = [s.vol_percentile for s in snaps.values() if np.isfinite(s.vol_percentile)]
    volq = float(np.clip(1.0 - np.mean(vp) / 100.0, 0, 1)) if vp else 0.5
    comp["volatility"] = volq * 100
    br = breadth.get("pct_above_50sma")
    comp["breadth"] = float(br) if br is not None else 50.0
    score = (0.30 * comp["trend"] + 0.20 * comp["ma_structure"] + 0.20 * comp["drawdown"]
             + 0.15 * comp["volatility"] + 0.15 * comp["breadth"])
    return float(np.clip(score, 0, 100)), comp


def compute_breadth(hub: DataHub, as_of: dt.date, symbols: list[str] | None = None,
                    max_symbols: int = 200) -> dict[str, Any]:
    """Percentage of the available universe above its own 50/200 SMA on ``as_of``."""
    syms = (symbols or hub.universe())[:max_symbols]
    above50 = above200 = newhigh = counted = 0
    for sym in syms:
        try:
            s = hub.daily(sym, end=as_of)
        except Exception:  # noqa: BLE001 - a missing symbol must not break breadth
            continue
        if len(s) < 60:
            continue
        c = s.close
        counted += 1
        m50 = sma(c, 50)[-1]
        m200 = sma(c, 200)[-1] if len(s) >= 200 else np.nan
        if np.isfinite(m50) and c[-1] > m50:
            above50 += 1
        if np.isfinite(m200) and c[-1] > m200:
            above200 += 1
        look = min(252, len(s))
        if c[-1] >= np.max(s.high[-look:]) * 0.98:
            newhigh += 1
    if not counted:
        return {"available": False, "reason": "no symbols with sufficient history"}
    return {
        "available": True, "symbols_counted": counted,
        "pct_above_50sma": round(100.0 * above50 / counted, 1),
        "pct_above_200sma": round(100.0 * above200 / counted, 1),
        "pct_within_2pct_of_52w_high": round(100.0 * newhigh / counted, 1),
    }


class RegimeClassifier:
    def __init__(self, hub: DataHub = HUB, params: RegimeParams = RegimeParams()) -> None:
        self.hub = hub
        self.p = params

    def classify(self, as_of: dt.date | None = None,
                 index_symbols: tuple[str, ...] = ("SPY", "QQQ"),
                 with_breadth: bool = True) -> MarketRegime:
        snaps: dict[str, RegimeSnapshot] = {}
        provider = origin = "unknown"
        date_str = as_of.isoformat() if as_of else ""
        for sym in index_symbols:
            try:
                s = self.hub.daily(sym, end=as_of)
            except Exception:  # noqa: BLE001
                continue
            if len(s) < self.p.slow + 2:
                continue
            provider, origin = s.provider, s.origin.value
            snaps[sym] = _snapshot(sym, s, len(s) - 1, self.p)
            date_str = snaps[sym].date

        if not snaps:
            return MarketRegime(
                as_of=date_str or (as_of.isoformat() if as_of else ""), label="UNKNOWN",
                volatility_label="UNKNOWN", strength=float("nan"), indexes={},
                breadth={"available": False, "reason": "no index data"},
                filter_pass=False,
                filter_detail="No index data available; the market filter cannot be evaluated.",
                data_origin="UNKNOWN", data_provider="none", components={})

        breadth = (compute_breadth(self.hub, as_of or dt.date.fromisoformat(date_str))
                   if with_breadth else {"available": False, "reason": "not requested"})
        strength, comp = _strength(snaps, breadth)

        worst = min(snaps.values(), key=lambda s: _ORDER[s.label])
        best = max(snaps.values(), key=lambda s: _ORDER[s.label])
        label = worst.label if _ORDER[best.label] - _ORDER[worst.label] <= 1 else "SIDEWAYS"

        vps = [s.vol_percentile for s in snaps.values() if np.isfinite(s.vol_percentile)]
        vp = float(np.mean(vps)) if vps else float("nan")
        vol_label = ("HIGH" if vp >= 80 else "LOW" if vp <= 20 else "NORMAL") if np.isfinite(vp) else "UNKNOWN"

        passing = [s.symbol for s in snaps.values() if s.fast_above_slow]
        failing = [s.symbol for s in snaps.values() if not s.fast_above_slow]
        detail = (f"{self.p.fast} SMA above {self.p.slow} SMA on: "
                  f"{', '.join(passing) or 'none'}; below on: {', '.join(failing) or 'none'}.")
        return MarketRegime(
            as_of=date_str, label=label, volatility_label=vol_label, strength=strength,
            indexes=snaps, breadth=breadth, filter_pass=not failing, filter_detail=detail,
            data_origin=origin, data_provider=provider, components=comp)


def classify_market(as_of: dt.date | None = None, hub: DataHub = HUB,
                    with_breadth: bool = True) -> MarketRegime:
    return RegimeClassifier(hub).classify(as_of, with_breadth=with_breadth)


def regime_series(symbol: str = "SPY", hub: DataHub = HUB,
                  params: RegimeParams = RegimeParams()) -> dict[str, np.ndarray]:
    """Vectorised per-bar regime inputs for one index -- what the backtester uses.

    Returns arrays aligned to the symbol's own bar index, so a backtest standing
    on bar ``i`` can read ``filter_pass[i]`` with no look-ahead.
    """
    s = hub.daily(symbol)
    c = s.close
    f, sl = sma(c, params.fast), sma(c, params.slow)
    long = sma(c, params.long)
    hi = rolling_max(c, params.drawdown_lookback)
    with np.errstate(invalid="ignore"):
        dd = (c / hi - 1.0) * 100.0
    return {
        "dates": np.array([t.date().toordinal() for t in s.ts]),
        "close": c, "fast": f, "slow": sl, "long": long,
        "fast_above_slow": (f > sl) & np.isfinite(f) & np.isfinite(sl),
        "close_above_long": (c > long) & np.isfinite(long),
        "drawdown_pct": dd,
    }
