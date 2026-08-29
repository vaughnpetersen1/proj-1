"""Cross-sectional panel.

Relative strength, sector ranking and breadth are all *cross-sectional*
questions: they compare a symbol to its peers on the same date. This aligns
every symbol's series to one master calendar so those comparisons are exact and
point-in-time (column ``t`` of the panel contains only information known at the
close of date ``t``).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Iterable

import numpy as np

from ..indicators.core import sma
from .providers.registry import HUB, DataHub
from .types import PriceSeries


@dataclasses.dataclass
class Panel:
    dates: list[dt.date]
    symbols: list[str]
    close: np.ndarray          # (n_dates, n_symbols), nan where the symbol had no bar
    volume: np.ndarray
    high: np.ndarray
    low: np.ndarray
    provider: str
    origin: str

    @property
    def shape(self) -> tuple[int, int]:
        return self.close.shape

    def col(self, symbol: str) -> int:
        return self.symbols.index(symbol.upper())

    def row(self, when: dt.date) -> int:
        lo, hi, ans = 0, len(self.dates) - 1, -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.dates[mid] <= when:
                ans, lo = mid, mid + 1
            else:
                hi = mid - 1
        return ans

    # -- derived measures ---------------------------------------------------
    def returns(self, lookback: int) -> np.ndarray:
        """Trailing ``lookback``-bar percent return for every symbol on every date."""
        out = np.full(self.close.shape, np.nan)
        if len(self.dates) > lookback:
            prev = self.close[:-lookback]
            with np.errstate(divide="ignore", invalid="ignore"):
                out[lookback:] = (self.close[lookback:] / prev - 1.0) * 100.0
        return out

    def rank_percentile(self, values: np.ndarray) -> np.ndarray:
        """Row-wise percentile rank (0-100), nan-safe.

        Row ``t`` ranks each symbol against the other symbols that had data on
        date ``t`` -- never against the future, never against a survivor list
        drawn from a later date.
        """
        out = np.full(values.shape, np.nan)
        for t in range(values.shape[0]):
            row = values[t]
            ok = np.isfinite(row)
            k = int(ok.sum())
            if k < 2:
                continue
            order = np.argsort(np.argsort(row[ok]))
            out[t, ok] = order / (k - 1) * 100.0
        return out

    def above_sma_fraction(self, length: int) -> np.ndarray:
        """Per-date fraction of symbols trading above their own N-bar SMA."""
        n_dates, n_syms = self.close.shape
        above = np.full(self.close.shape, np.nan)
        for j in range(n_syms):
            col = self.close[:, j]
            ok = np.isfinite(col)
            if ok.sum() < length + 1:
                continue
            m = np.full(n_dates, np.nan)
            m[ok] = sma(col[ok], length)
            above[:, j] = np.where(np.isfinite(m), (col > m).astype(float), np.nan)
        import warnings
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-nan rows are legitimate
            return np.nanmean(above, axis=1) * 100.0


def build_panel(symbols: Iterable[str] | None = None, hub: DataHub = HUB,
                start: dt.date | None = None, end: dt.date | None = None,
                max_symbols: int | None = None) -> Panel:
    syms = [s.upper() for s in (symbols if symbols is not None else hub.universe())]
    if max_symbols:
        syms = syms[:max_symbols]
    loaded: dict[str, PriceSeries] = {}
    for s in syms:
        try:
            ser = hub.daily(s, start, end)
        except Exception:  # noqa: BLE001 -- a symbol the data path cannot serve is skipped
            continue
        if len(ser):
            loaded[s] = ser
    if not loaded:
        raise ValueError("panel would be empty: no symbol could be loaded")

    all_dates = sorted({t.date() for ser in loaded.values() for t in ser.ts})
    pos = {d: i for i, d in enumerate(all_dates)}
    syms = sorted(loaded)
    n, m = len(all_dates), len(syms)
    close = np.full((n, m), np.nan)
    volume = np.full((n, m), np.nan)
    high = np.full((n, m), np.nan)
    low = np.full((n, m), np.nan)
    for j, s in enumerate(syms):
        ser = loaded[s]
        rows = np.array([pos[t.date()] for t in ser.ts])
        close[rows, j] = ser.close
        volume[rows, j] = ser.volume
        high[rows, j] = ser.high
        low[rows, j] = ser.low
    any_ser = next(iter(loaded.values()))
    return Panel(dates=all_dates, symbols=syms, close=close, volume=volume,
                 high=high, low=low, provider=any_ser.provider, origin=any_ser.origin.value)
