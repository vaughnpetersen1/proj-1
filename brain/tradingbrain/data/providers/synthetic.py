"""Deterministic synthetic market generator.

WHY THIS EXISTS
---------------
This repository must be developable and testable with zero API keys and zero
network egress (the environment it was built in has both blocked). Rather than
stopping, we generate a *self-consistent* fake market.

HARD RULE: every series produced here is stamped ``DataOrigin.SYNTHETIC`` and
every downstream Claim built from it automatically grows the caveat "Derived
from SYNTHETIC data. Not evidence about real markets." Nothing in this file may
ever be presented as a market observation.

The generator is intentionally structured -- market factor, sector factors,
idiosyncratic noise, momentum episodes, consolidations, earnings gaps -- so the
regime engine, sector engine, setup detectors and backtester are all exercised
against data that has the *shape* of equities, not white noise. That makes the
engines testable; it does NOT make their outputs evidence.
"""

from __future__ import annotations

import datetime as dt
import functools
import math
from typing import Any

import numpy as np

from ...provenance import DataOrigin
from ..types import Bar, PriceSeries, Timeframe
from .base import HistoricalDataProvider, IntradayDataProvider, SectorDataProvider

START = dt.date(2010, 1, 4)
END = dt.date(2026, 8, 28)


def _n_business_days(start: dt.date, end: dt.date) -> int:
    n, d = 0, start
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += dt.timedelta(days=1)
    return n


N_DAYS = _n_business_days(START, END)
YEARS = N_DAYS / 252.0

SECTORS: dict[str, float] = {
    "Technology": 1.25,
    "Semiconductors": 1.55,
    "Communication Services": 1.05,
    "Consumer Discretionary": 1.10,
    "Consumer Staples": 0.55,
    "Health Care": 0.80,
    "Financials": 1.05,
    "Industrials": 1.00,
    "Energy": 1.15,
    "Materials": 0.95,
    "Utilities": 0.45,
    "Real Estate": 0.90,
}

SECTOR_ETF = {
    "Technology": "XLK", "Semiconductors": "SMH", "Communication Services": "XLC",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Health Care": "XLV",
    "Financials": "XLF", "Industrials": "XLI", "Energy": "XLE", "Materials": "XLB",
    "Utilities": "XLU", "Real Estate": "XLRE",
}

THEMES = {
    "AI Infrastructure": ["NVDA", "AMD", "AVGO", "SMCI", "VRT", "MRVL", "ANET"],
    "Software": ["MSFT", "CRM", "NOW", "PLTR", "SNOW", "DDOG", "MDB"],
    "Weight Loss": ["LLY", "NVO", "VKTX", "ZLDPF"],
    "Nuclear / Power": ["CEG", "VST", "OKLO", "SMR", "TLN"],
    "Crypto Adjacent": ["COIN", "MARA", "RIOT", "MSTR", "CLSK"],
    "Mega Cap": ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA"],
    "Energy": ["XOM", "CVX", "SLB", "FANG", "OXY"],
    "Defense": ["LMT", "RTX", "NOC", "GD"],
    "Banks": ["JPM", "BAC", "WFC", "GS", "MS"],
    "Retail": ["WMT", "COST", "TGT", "HD", "LOW"],
    "Biotech Momentum": ["VKTX", "CRSP", "MRNA", "ALNY"],
}

# symbol -> (sector, beta, idio_vol_annual, momentum_propensity)
UNIVERSE_SPEC: dict[str, tuple[str, float, float, float]] = {
    "AAPL": ("Technology", 1.15, 0.22, 0.5),
    "MSFT": ("Technology", 1.05, 0.20, 0.5),
    "NVDA": ("Semiconductors", 1.75, 0.45, 1.6),
    "AMD": ("Semiconductors", 1.80, 0.48, 1.4),
    "AVGO": ("Semiconductors", 1.35, 0.30, 1.0),
    "SMCI": ("Semiconductors", 2.10, 0.75, 2.0),
    "MRVL": ("Semiconductors", 1.60, 0.44, 1.1),
    "ANET": ("Technology", 1.30, 0.34, 1.0),
    "VRT": ("Industrials", 1.60, 0.52, 1.5),
    "AMZN": ("Consumer Discretionary", 1.25, 0.28, 0.7),
    "GOOGL": ("Communication Services", 1.10, 0.24, 0.5),
    "META": ("Communication Services", 1.30, 0.34, 0.9),
    "TSLA": ("Consumer Discretionary", 1.90, 0.55, 1.3),
    "CRM": ("Technology", 1.20, 0.30, 0.6),
    "NOW": ("Technology", 1.15, 0.28, 0.7),
    "PLTR": ("Technology", 1.70, 0.58, 1.7),
    "SNOW": ("Technology", 1.55, 0.50, 0.9),
    "DDOG": ("Technology", 1.50, 0.46, 0.9),
    "MDB": ("Technology", 1.55, 0.52, 0.9),
    "LLY": ("Health Care", 0.85, 0.26, 1.1),
    "NVO": ("Health Care", 0.80, 0.27, 0.9),
    "VKTX": ("Health Care", 1.40, 0.85, 2.2),
    "CRSP": ("Health Care", 1.35, 0.70, 1.4),
    "MRNA": ("Health Care", 1.20, 0.60, 1.2),
    "ALNY": ("Health Care", 0.95, 0.42, 0.8),
    "CEG": ("Utilities", 0.90, 0.36, 1.4),
    "VST": ("Utilities", 0.95, 0.40, 1.5),
    "OKLO": ("Utilities", 1.60, 0.95, 2.3),
    "SMR": ("Utilities", 1.55, 0.90, 2.1),
    "TLN": ("Utilities", 1.00, 0.44, 1.3),
    "COIN": ("Financials", 2.00, 0.72, 1.8),
    "MARA": ("Financials", 2.30, 1.00, 2.4),
    "RIOT": ("Financials", 2.25, 0.98, 2.3),
    "MSTR": ("Technology", 2.20, 0.90, 2.2),
    "CLSK": ("Financials", 2.30, 1.05, 2.4),
    "XOM": ("Energy", 0.85, 0.24, 0.4),
    "CVX": ("Energy", 0.85, 0.23, 0.4),
    "SLB": ("Energy", 1.15, 0.32, 0.6),
    "FANG": ("Energy", 1.10, 0.34, 0.6),
    "OXY": ("Energy", 1.20, 0.36, 0.6),
    "LMT": ("Industrials", 0.60, 0.20, 0.4),
    "RTX": ("Industrials", 0.70, 0.21, 0.4),
    "NOC": ("Industrials", 0.65, 0.22, 0.4),
    "GD": ("Industrials", 0.70, 0.20, 0.4),
    "JPM": ("Financials", 1.05, 0.24, 0.4),
    "BAC": ("Financials", 1.15, 0.27, 0.4),
    "WFC": ("Financials", 1.10, 0.27, 0.4),
    "GS": ("Financials", 1.20, 0.28, 0.5),
    "MS": ("Financials", 1.15, 0.27, 0.5),
    "WMT": ("Consumer Staples", 0.55, 0.17, 0.4),
    "COST": ("Consumer Staples", 0.65, 0.18, 0.5),
    "TGT": ("Consumer Discretionary", 0.90, 0.28, 0.4),
    "HD": ("Consumer Discretionary", 0.95, 0.22, 0.4),
    "LOW": ("Consumer Discretionary", 0.95, 0.24, 0.4),
    "CAT": ("Industrials", 1.10, 0.26, 0.6),
    "DE": ("Industrials", 1.00, 0.26, 0.5),
    "UNH": ("Health Care", 0.75, 0.22, 0.4),
    "PFE": ("Health Care", 0.70, 0.24, 0.3),
    "NEE": ("Utilities", 0.60, 0.22, 0.4),
    "PLD": ("Real Estate", 1.00, 0.26, 0.4),
    "AMT": ("Real Estate", 0.75, 0.24, 0.3),
    "FCX": ("Materials", 1.35, 0.38, 0.8),
    "NEM": ("Materials", 0.85, 0.35, 0.7),
    "LIN": ("Materials", 0.85, 0.20, 0.4),
    "DIS": ("Communication Services", 1.10, 0.28, 0.4),
    "NFLX": ("Communication Services", 1.25, 0.36, 0.9),
    "UBER": ("Industrials", 1.35, 0.38, 0.9),
    "ABNB": ("Consumer Discretionary", 1.30, 0.38, 0.7),
    "SHOP": ("Technology", 1.70, 0.55, 1.2),
    "RBLX": ("Communication Services", 1.60, 0.58, 1.1),
    "SOFI": ("Financials", 1.75, 0.66, 1.4),
    "AFRM": ("Financials", 2.00, 0.80, 1.7),
    "HOOD": ("Financials", 1.85, 0.72, 1.6),
    "IONQ": ("Technology", 2.00, 1.00, 2.3),
    "RGTI": ("Technology", 2.10, 1.10, 2.4),
    "ACHR": ("Industrials", 1.90, 0.95, 2.0),
    "JOBY": ("Industrials", 1.85, 0.92, 2.0),
    "LUNR": ("Industrials", 1.95, 1.05, 2.2),
    "RKLB": ("Industrials", 1.80, 0.80, 1.8),
}

INDEXES = {"SPY": 1.00, "QQQ": 1.18, "IWM": 1.10, "DIA": 0.92, "MDY": 1.02}


def business_days(start: dt.date, n: int) -> list[dt.date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _retarget(rets: np.ndarray, target_annual_log: float) -> np.ndarray:
    """Shift a return series by a constant so its annualised log drift equals
    ``target_annual_log``. Structure (episodes, consolidations, vol clustering)
    is untouched; only the level of the drift changes."""
    n = len(rets)
    if n == 0:
        return rets
    want = target_annual_log * (n / 252.0)
    return rets + (want - float(np.sum(rets))) / n


def _bound_excursion(rets: np.ndarray, cap_log: float = 1.6) -> np.ndarray:
    """Soft-clip the cumulative path's deviation from its own trend line.

    Without this, sixteen years of stacked momentum episodes compound into
    prices no equity ever prints. ``tanh`` leaves ordinary excursions almost
    untouched and only compresses runaway ones, so consolidation/breakout
    structure survives."""
    n = len(rets)
    if n < 3:
        return rets
    path = np.cumsum(rets)
    trend = np.linspace(path[0], path[-1], n)
    dev = path - trend
    path2 = trend + cap_log * np.tanh(dev / cap_log)
    return np.diff(np.concatenate([[0.0], path2]))


def _seed(*parts: Any) -> int:
    h = 2166136261
    for p in parts:
        for ch in str(p):
            h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h


class _Market:
    """Generated once, memoised. Pure function of the constants above."""

    def __init__(self) -> None:
        self.dates = business_days(START, N_DAYS)
        n = N_DAYS
        rng = np.random.default_rng(_seed("market", START, n))

        # --- market factor: 3-state regime switching -----------------------
        # states: 0 bull, 1 chop, 2 bear.  drift/vol per state (daily).
        drift = np.array([0.00075, 0.00005, -0.00110])
        vol = np.array([0.0085, 0.0105, 0.0195])
        trans = np.array([
            [0.988, 0.010, 0.002],
            [0.030, 0.950, 0.020],
            [0.020, 0.060, 0.920],
        ])
        state = 0
        states = np.empty(n, dtype=int)
        mkt = np.empty(n)
        for i in range(n):
            states[i] = state
            mkt[i] = drift[state] + vol[state] * rng.standard_normal()
            state = int(rng.choice(3, p=trans[state]))
        # Normalise the market to a plausible long-run drift without touching
        # its regime structure: a constant drag preserves every path feature.
        mkt = _retarget(mkt, target_annual_log=0.085)
        self.market_returns = mkt
        self.market_states = states

        # --- sector factors ------------------------------------------------
        self.sector_returns: dict[str, np.ndarray] = {}
        for si, (sector, sbeta) in enumerate(SECTORS.items()):
            srng = np.random.default_rng(_seed("sector", sector))
            # slow rotating sector alpha: sum of low-frequency sinusoids + noise
            t = np.arange(n)
            alpha = np.zeros(n)
            for k in range(3):
                period = 180 + 340 * srng.random()
                phase = 2 * math.pi * srng.random()
                amp = 0.00045 * (0.5 + srng.random())
                alpha += amp * np.sin(2 * math.pi * t / period + phase)
            resid = 0.0060 * srng.standard_normal(n)
            self.sector_returns[sector] = sbeta * mkt + alpha + resid

        # --- symbols ---------------------------------------------------------
        self.symbol_series: dict[str, np.ndarray] = {}
        self.symbol_volume: dict[str, np.ndarray] = {}
        for sym, (sector, beta, idio, mom) in UNIVERSE_SPEC.items():
            self.symbol_series[sym], self.symbol_volume[sym] = self._make_symbol(
                sym, sector, beta, idio, mom, n
            )
        for sym, beta in INDEXES.items():
            r = beta * mkt + 0.0018 * np.random.default_rng(_seed("idx", sym)).standard_normal(n)
            r = _retarget(r, target_annual_log=0.075 + 0.03 * (beta - 1))
            self.symbol_series[sym] = self._to_prices(sym, r, base=100.0 + 40 * (beta - 1))
            self.symbol_volume[sym] = self._make_volume(sym, r, base=7.5e7)
        for sector, etf in SECTOR_ETF.items():
            r = self.sector_returns[sector]
            self.symbol_series[etf] = self._to_prices(etf, r, base=60.0)
            self.symbol_volume[etf] = self._make_volume(etf, r, base=1.2e7)

    # -- helpers -----------------------------------------------------------
    def _to_prices(self, sym: str, rets: np.ndarray, base: float) -> np.ndarray:
        return base * np.exp(np.cumsum(rets))

    def _make_volume(self, sym: str, rets: np.ndarray, base: float) -> np.ndarray:
        rng = np.random.default_rng(_seed("vol", sym))
        n = len(rets)
        trend = base * np.exp(np.cumsum(0.00002 * rng.standard_normal(n)))
        shock = 1.0 + 3.2 * np.abs(rets) / (np.std(rets) + 1e-12) * 0.25
        noise = np.exp(0.30 * rng.standard_normal(n))
        return np.maximum(trend * shock * noise, 1000.0)

    def _make_symbol(self, sym: str, sector: str, beta: float, idio: float,
                     mom: float, n: int) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(_seed("sym", sym))
        daily_idio = idio / math.sqrt(252)
        rets = beta * self.sector_returns[sector] + daily_idio * rng.standard_normal(n)

        # --- momentum episodes: expansion -> consolidation -> resolution ----
        n_eps = int(round(mom * 9))
        for _ in range(n_eps):
            start = int(rng.integers(60, max(61, n - 200)))
            # 1. expansion leg (the "big prior move")
            exp_len = int(rng.integers(8, 34))
            exp_total = (0.20 + 0.95 * rng.random()) * (0.6 + mom / 2)
            leg = np.linspace(1.4, 0.6, exp_len)
            leg = leg / leg.sum() * math.log1p(exp_total)
            rets[start:start + exp_len] += leg[: max(0, min(exp_len, n - start))][: n - start]
            p = start + exp_len
            # 2. consolidation: drift ~0, volatility contracts
            cons_len = int(rng.integers(6, 40))
            end = min(n, p + cons_len)
            if end > p:
                damp = np.linspace(0.75, 0.30, end - p)
                rets[p:end] = rets[p:end] * damp - 0.00025
            q = end
            # 3. resolution: continuation or failure
            res_len = int(rng.integers(10, 45))
            e2 = min(n, q + res_len)
            if e2 > q:
                cont = rng.random() < 0.42
                mag = (0.18 + 0.70 * rng.random()) if cont else -(0.10 + 0.32 * rng.random())
                leg2 = np.linspace(1.5, 0.5, e2 - q)
                leg2 = leg2 / leg2.sum() * math.log1p(max(mag, -0.9))
                rets[q:e2] += leg2

        # --- episodic-pivot style catalyst gaps ------------------------------
        n_gaps = int(round(mom * 3.0)) + 1
        gap_idx = rng.integers(40, n - 5, size=n_gaps)
        for gi in gap_idx:
            sign = 1.0 if rng.random() < 0.62 else -1.0
            rets[gi] += sign * (0.06 + 0.30 * rng.random())

        # Constant drag so 16 years of stacked momentum episodes do not compound
        # into absurd prices. Preserves episode/consolidation structure exactly.
        target = float(np.clip(rng.uniform(-0.10, 0.20) + 0.02 * mom, -0.12, 0.18))
        rets = _bound_excursion(_retarget(rets, target_annual_log=target))

        base = float(6.0 + 190.0 * rng.random())
        prices = self._to_prices(sym, rets, base=base)
        vol_base = float(np.interp(base, [5, 50, 200, 400], [9e6, 4e6, 2.5e6, 1.2e6]))
        volume = self._make_volume(sym, rets, base=vol_base)
        return prices, volume

    def ohlcv(self, symbol: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        sym = symbol.upper()
        close = self.symbol_series[sym]
        volume = self.symbol_volume[sym]
        rng = np.random.default_rng(_seed("ohlc", sym))
        n = len(close)
        prev = np.concatenate([[close[0]], close[:-1]])
        rets = close / prev - 1.0
        # realistic intraday range proportional to |ret| plus a floor
        rough = np.abs(rets) + 0.006 + 0.010 * rng.random(n)
        # open gaps a fraction of the way toward the close
        gap_frac = 0.25 + 0.5 * rng.random(n)
        open_ = prev * (1 + rets * gap_frac * 0.9)
        hi_ext = rough * (0.35 + 0.55 * rng.random(n))
        lo_ext = rough * (0.35 + 0.55 * rng.random(n))
        high = np.maximum(open_, close) * (1 + hi_ext * 0.5)
        low = np.minimum(open_, close) * (1 - lo_ext * 0.5)
        low = np.minimum(low, np.minimum(open_, close))
        high = np.maximum(high, np.maximum(open_, close))
        return open_, high, low, close, volume


@functools.lru_cache(maxsize=1)
def market() -> _Market:
    return _Market()


class SyntheticProvider(HistoricalDataProvider, IntradayDataProvider, SectorDataProvider):
    """Generated data. Always labelled SYNTHETIC."""

    name = "synthetic"
    synthetic = True

    def available(self) -> tuple[bool, str]:
        return True, ("Deterministic generated market. Development/testing only -- "
                      "results are NOT evidence about real markets.")

    def symbols(self) -> list[str]:
        m = market()
        return sorted(m.symbol_series)

    def sector_map(self) -> dict[str, str]:
        out = {s: spec[0] for s, spec in UNIVERSE_SPEC.items()}
        for sector, etf in SECTOR_ETF.items():
            out[etf] = sector
        return out

    def daily(self, symbol: str, start: dt.date | None = None,
              end: dt.date | None = None) -> PriceSeries:
        m = market()
        sym = symbol.upper()
        if sym not in m.symbol_series:
            raise KeyError(f"{sym} is not in the synthetic universe")
        o, h, l, c, v = m.ohlcv(sym)
        bars = [
            Bar(dt.datetime.combine(d, dt.time(16, 0)), float(o[i]), float(h[i]),
                float(l[i]), float(c[i]), float(v[i]))
            for i, d in enumerate(m.dates)
        ]
        ps = PriceSeries.from_bars(sym, Timeframe.D1, bars, provider=self.name,
                                   origin=DataOrigin.SYNTHETIC, adjusted=True)
        return ps.between(start, end)

    def intraday(self, symbol: str, timeframe: Timeframe,
                 start: dt.date | None = None, end: dt.date | None = None) -> PriceSeries:
        """Build a within-day path whose OHLC reconciles to the daily bar."""
        if not timeframe.is_intraday:
            return self.daily(symbol, start, end)
        daily = self.daily(symbol, start, end)
        per_day = max(1, 390 // timeframe.minutes)
        bars: list[Bar] = []
        for i in range(len(daily)):
            day = daily.ts[i].date()
            rng = np.random.default_rng(_seed("intra", symbol.upper(), day.isoformat()))
            o, h, l, c = (float(daily.open[i]), float(daily.high[i]),
                          float(daily.low[i]), float(daily.close[i]))
            path = self._day_path(rng, o, h, l, c, per_day)
            dayvol = float(daily.volume[i])
            # U-shaped volume profile
            prof = np.array([1.7 * math.exp(-3 * k / per_day) + 0.55
                             + 1.2 * math.exp(-3 * (per_day - 1 - k) / per_day)
                             for k in range(per_day)])
            prof = prof / prof.sum()
            t0 = dt.datetime.combine(day, dt.time(9, 30))
            for k in range(per_day):
                po, ph, pl, pc = path[k]
                bars.append(Bar(t0 + dt.timedelta(minutes=timeframe.minutes * k),
                                po, ph, pl, pc, dayvol * float(prof[k])))
        return PriceSeries.from_bars(symbol.upper(), timeframe, bars, provider=self.name,
                                     origin=DataOrigin.SYNTHETIC, adjusted=True)

    @staticmethod
    def _day_path(rng, o: float, h: float, l: float, c: float,
                  n: int) -> list[tuple[float, float, float, float]]:
        """Brownian bridge from o to c, rescaled so the path touches h and l."""
        steps = rng.standard_normal(n)
        steps = steps - steps.mean()
        cum = np.cumsum(steps)
        cum = cum - np.linspace(0, cum[-1], n)  # bridge back to zero
        span = (h - l) or max(c * 0.001, 0.01)
        scale = span / max(cum.max() - cum.min(), 1e-9) * 0.72
        base = np.linspace(o, c, n) + cum * scale
        base = np.clip(base, l, h)
        # ensure the extremes are actually visited
        base[int(rng.integers(0, n))] = h if rng.random() < 0.5 else l
        out = []
        for k in range(n):
            po = base[k - 1] if k else o
            pc = base[k] if k < n - 1 else c
            wig = abs(pc - po) * 0.5 + span * 0.02
            ph = max(po, pc) + wig * rng.random()
            pl = min(po, pc) - wig * rng.random()
            out.append((float(po), float(min(ph, h)), float(max(pl, l)), float(pc)))
        return out
