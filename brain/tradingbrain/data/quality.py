"""Data-quality validation.

Backtests are only as trustworthy as the bars underneath them. Every series that
enters an engine can be run through :func:`validate` and the resulting report is
attached to backtest output so a suspicious result can be traced to bad data
rather than being explained away.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import numpy as np

from .types import PriceSeries, Timeframe


@dataclasses.dataclass
class QualityIssue:
    code: str
    severity: str            # "error" | "warning" | "info"
    detail: str
    count: int = 1
    sample: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class QualityReport:
    symbol: str
    timeframe: str
    bars: int
    issues: list[QualityIssue]

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "timeframe": self.timeframe, "bars": self.bars,
                "ok": self.ok, "issues": [i.to_dict() for i in self.issues]}


def _sample(ts: list[dt.datetime], idx: np.ndarray, k: int = 5) -> list[str]:
    return [ts[int(i)].isoformat() for i in idx[:k]]


def validate(series: PriceSeries, *, max_gap_days: int = 6,
             split_threshold: float = 0.45) -> QualityReport:
    issues: list[QualityIssue] = []
    n = len(series)
    if n == 0:
        return QualityReport(series.symbol, series.timeframe.value, 0,
                             [QualityIssue("empty", "error", "series contains no bars")])

    ts = series.ts
    o, h, l, c, v = series.open, series.high, series.low, series.close, series.volume

    # --- ordering & duplicates -------------------------------------------
    arr = np.array([t.timestamp() for t in ts])
    d = np.diff(arr)
    if np.any(d < 0):
        issues.append(QualityIssue("unsorted", "error", "timestamps are not monotonically increasing",
                                   int(np.sum(d < 0))))
    dup = np.flatnonzero(d == 0)
    if dup.size:
        issues.append(QualityIssue("duplicate_timestamps", "error",
                                   "repeated timestamps -- duplicate candles",
                                   int(dup.size), _sample(ts, dup)))

    # --- OHLC internal consistency ---------------------------------------
    bad = np.flatnonzero((h < l) | (h < o - 1e-9) | (h < c - 1e-9) |
                         (l > o + 1e-9) | (l > c + 1e-9))
    if bad.size:
        issues.append(QualityIssue("ohlc_inconsistent", "error",
                                   "high/low do not bracket open/close",
                                   int(bad.size), _sample(ts, bad)))

    nonpos = np.flatnonzero((c <= 0) | (o <= 0) | (h <= 0) | (l <= 0))
    if nonpos.size:
        issues.append(QualityIssue("nonpositive_price", "error", "non-positive prices",
                                   int(nonpos.size), _sample(ts, nonpos)))

    nan = np.flatnonzero(~np.isfinite(c) | ~np.isfinite(o) | ~np.isfinite(h) | ~np.isfinite(l))
    if nan.size:
        issues.append(QualityIssue("nan_price", "error", "NaN/inf in price fields",
                                   int(nan.size), _sample(ts, nan)))

    # --- calendar gaps (daily only) --------------------------------------
    if series.timeframe is Timeframe.D1 and n > 2:
        gaps = np.flatnonzero(d > max_gap_days * 86400)
        if gaps.size:
            issues.append(QualityIssue(
                "calendar_gap", "warning",
                f"{gaps.size} gap(s) longer than {max_gap_days} calendar days -- "
                "missing candles or a trading halt",
                int(gaps.size), _sample(ts, gaps + 1)))

    # --- suspected unadjusted splits/dividends ---------------------------
    if n > 2:
        with np.errstate(divide="ignore", invalid="ignore"):
            step = c[1:] / c[:-1]
        jumps = np.flatnonzero((step < split_threshold) | (step > 1.0 / split_threshold))
        if jumps.size:
            sev = "warning" if series.adjusted else "error"
            issues.append(QualityIssue(
                "possible_unadjusted_corporate_action", sev,
                "close-to-close moves beyond +/-{:.0%} -- likely an unadjusted split or "
                "reverse split. Backtests on unadjusted data produce fictitious returns."
                .format(1 / split_threshold - 1),
                int(jumps.size), _sample(ts, jumps + 1)))

    # --- volume ------------------------------------------------------------
    zerov = np.flatnonzero(v <= 0)
    if zerov.size > n * 0.02:
        issues.append(QualityIssue("zero_volume", "warning",
                                   "more than 2% of bars have zero/negative volume",
                                   int(zerov.size), _sample(ts, zerov)))

    # --- session sanity (intraday) ----------------------------------------
    if series.timeframe.is_intraday:
        odd = np.flatnonzero(np.array([
            not (dt.time(4, 0) <= t.time() <= dt.time(20, 0)) for t in ts]))
        if odd.size:
            issues.append(QualityIssue("outside_session", "warning",
                                       "bars stamped outside 04:00-20:00 US session window",
                                       int(odd.size), _sample(ts, odd)))

    if not series.adjusted:
        issues.append(QualityIssue(
            "unadjusted", "warning",
            "series is not marked split/dividend adjusted; long-horizon statistics "
            "computed on it will be biased"))

    if series.origin.value == "SYNTHETIC":
        issues.append(QualityIssue("synthetic", "info",
                                   "generated data -- valid for exercising code, "
                                   "not evidence about real markets"))
    return QualityReport(series.symbol, series.timeframe.value, n, issues)


def survivorship_note(universe_source: str) -> QualityIssue:
    """The bias no validator can detect from the bars themselves."""
    return QualityIssue(
        "survivorship_bias", "warning",
        f"Universe '{universe_source}' is a present-day symbol list. Any backtest over it "
        "excludes delisted/acquired/bankrupt names and will overstate returns. Use a "
        "point-in-time constituent file to remove this bias.")
