"""Core market-data containers.

``PriceSeries`` is deliberately dumb and immutable-ish: numpy arrays plus a
date index plus provenance. Every consumer that needs point-in-time safety goes
through :meth:`PriceSeries.upto`, which is the only sanctioned way to look at
"the data as it would have been known on day i".
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
from typing import Iterator, Sequence

import numpy as np

from ..provenance import DataOrigin


class Timeframe(str, enum.Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    D1 = "1d"
    W1 = "1w"

    @property
    def is_intraday(self) -> bool:
        return self in (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1)

    @property
    def minutes(self) -> int:
        return {
            Timeframe.M1: 1,
            Timeframe.M5: 5,
            Timeframe.M15: 15,
            Timeframe.H1: 60,
            Timeframe.D1: 390,
            Timeframe.W1: 1950,
        }[self]


@dataclasses.dataclass(frozen=True)
class Bar:
    ts: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def as_tuple(self) -> tuple[dt.datetime, float, float, float, float, float]:
        return (self.ts, self.open, self.high, self.low, self.close, self.volume)


@dataclasses.dataclass
class PriceSeries:
    """An OHLCV series for one symbol on one timeframe."""

    symbol: str
    timeframe: Timeframe
    ts: list[dt.datetime]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    provider: str = "unknown"
    origin: DataOrigin = DataOrigin.UNKNOWN
    adjusted: bool = False
    warnings: list[str] = dataclasses.field(default_factory=list)

    # -- construction ------------------------------------------------------
    @classmethod
    def from_bars(
        cls,
        symbol: str,
        timeframe: Timeframe,
        bars: Sequence[Bar],
        provider: str = "unknown",
        origin: DataOrigin = DataOrigin.UNKNOWN,
        adjusted: bool = False,
    ) -> "PriceSeries":
        bars = sorted(bars, key=lambda b: b.ts)
        return cls(
            symbol=symbol.upper(),
            timeframe=timeframe,
            ts=[b.ts for b in bars],
            open=np.array([b.open for b in bars], dtype=float),
            high=np.array([b.high for b in bars], dtype=float),
            low=np.array([b.low for b in bars], dtype=float),
            close=np.array([b.close for b in bars], dtype=float),
            volume=np.array([b.volume for b in bars], dtype=float),
            provider=provider,
            origin=origin,
            adjusted=adjusted,
        )

    # -- basics ------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.ts)

    def __iter__(self) -> Iterator[Bar]:
        for i in range(len(self)):
            yield self.bar(i)

    def bar(self, i: int) -> Bar:
        return Bar(self.ts[i], float(self.open[i]), float(self.high[i]),
                   float(self.low[i]), float(self.close[i]), float(self.volume[i]))

    @property
    def dates(self) -> list[dt.date]:
        return [t.date() for t in self.ts]

    def index_of(self, when: dt.date | dt.datetime) -> int:
        """Index of the last bar at or before ``when``. -1 if none."""
        target = when if isinstance(when, dt.datetime) else dt.datetime.combine(when, dt.time.max)
        lo, hi, ans = 0, len(self.ts) - 1, -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.ts[mid] <= target:
                ans, lo = mid, mid + 1
            else:
                hi = mid - 1
        return ans

    # -- point-in-time -----------------------------------------------------
    def upto(self, i: int) -> "PriceSeries":
        """The series as known at the *close* of bar ``i`` (inclusive).

        This is the guard against look-ahead bias. Slicing directly is allowed
        internally but every strategy/scan/statistics path is written against
        this method so that leakage shows up as an obvious violation in review.
        """
        if i < 0:
            i = -1
        j = i + 1
        return dataclasses.replace(
            self,
            ts=self.ts[:j],
            open=self.open[:j], high=self.high[:j],
            low=self.low[:j], close=self.close[:j], volume=self.volume[:j],
        )

    def slice(self, start: int, end: int) -> "PriceSeries":
        return dataclasses.replace(
            self,
            ts=self.ts[start:end],
            open=self.open[start:end], high=self.high[start:end],
            low=self.low[start:end], close=self.close[start:end],
            volume=self.volume[start:end],
        )

    def between(self, start: dt.date | None, end: dt.date | None) -> "PriceSeries":
        lo = 0
        hi = len(self)
        if start is not None:
            while lo < hi and self.ts[lo].date() < start:
                lo += 1
        if end is not None:
            while hi > lo and self.ts[hi - 1].date() > end:
                hi -= 1
        return self.slice(lo, hi)

    def to_records(self) -> list[dict]:
        return [
            {
                "t": self.ts[i].isoformat(),
                "o": float(self.open[i]), "h": float(self.high[i]),
                "l": float(self.low[i]), "c": float(self.close[i]),
                "v": float(self.volume[i]),
            }
            for i in range(len(self))
        ]

    def describe(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe.value,
            "bars": len(self),
            "first": self.ts[0].isoformat() if len(self) else None,
            "last": self.ts[-1].isoformat() if len(self) else None,
            "provider": self.provider,
            "origin": self.origin.value,
            "adjusted": self.adjusted,
            "warnings": list(self.warnings),
        }
