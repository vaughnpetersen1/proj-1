"""Vectorised indicators.

Every function returns an array of the same length as its input, where element
``i`` is computed **only from elements <= i**. Warm-up positions are ``nan``.
That invariant is what makes it safe for the backtester to read
``indicator[i]`` while standing on bar ``i``.
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "sma", "ema", "rolling_max", "rolling_min", "true_range", "atr", "adr_pct",
    "rsi", "stdev", "relative_volume", "dollar_volume", "pct_change_n",
    "distance_pct", "slope_r2", "rolling_apply", "n_bars_above",
    "consecutive_true", "zscore", "linreg_slope",
]


def _as_float(a) -> np.ndarray:
    return np.asarray(a, dtype=float)


def sma(x, n: int) -> np.ndarray:
    x = _as_float(x)
    out = np.full(x.shape, np.nan)
    if n <= 0 or len(x) < n:
        return out
    cs = np.cumsum(np.insert(x, 0, 0.0))
    out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def ema(x, n: int) -> np.ndarray:
    x = _as_float(x)
    out = np.full(x.shape, np.nan)
    if n <= 0 or len(x) < n:
        return out
    k = 2.0 / (n + 1.0)
    acc = float(np.mean(x[:n]))
    out[n - 1] = acc
    for i in range(n, len(x)):
        acc = x[i] * k + acc * (1 - k)
        out[i] = acc
    return out


def _windows(x: np.ndarray, n: int):
    return np.lib.stride_tricks.sliding_window_view(x, n)


def rolling_max(x, n: int) -> np.ndarray:
    x = _as_float(x)
    out = np.full(x.shape, np.nan)
    if n <= 0 or len(x) < n:
        return out
    out[n - 1:] = _windows(x, n).max(axis=1)
    return out


def rolling_min(x, n: int) -> np.ndarray:
    x = _as_float(x)
    out = np.full(x.shape, np.nan)
    if n <= 0 or len(x) < n:
        return out
    out[n - 1:] = _windows(x, n).min(axis=1)
    return out


def true_range(high, low, close) -> np.ndarray:
    h, l, c = _as_float(high), _as_float(low), _as_float(close)
    prev = np.concatenate([[c[0]], c[:-1]]) if len(c) else c
    return np.maximum.reduce([h - l, np.abs(h - prev), np.abs(l - prev)])


def atr(high, low, close, n: int = 14) -> np.ndarray:
    """Wilder's ATR."""
    tr = true_range(high, low, close)
    out = np.full(tr.shape, np.nan)
    if len(tr) < n or n <= 0:
        return out
    acc = float(np.mean(tr[:n]))
    out[n - 1] = acc
    for i in range(n, len(tr)):
        acc = (acc * (n - 1) + tr[i]) / n
        out[i] = acc
    return out


def adr_pct(high, low, n: int = 20) -> np.ndarray:
    """Average Daily Range as a percentage of price.

    Kullamagi-style ADR: mean of (high/low) over n days, minus 1. This is the
    definition used in his public position-sizing material (stop no wider than
    1x ADR); it differs from ATR/close and the difference matters, so the two
    are kept separate rather than blended.
    """
    h, l = _as_float(high), _as_float(low)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(l > 0, h / l, np.nan)
    return (sma(ratio, n) - 1.0) * 100.0


def rsi(close, n: int = 14) -> np.ndarray:
    c = _as_float(close)
    out = np.full(c.shape, np.nan)
    if len(c) <= n:
        return out
    delta = np.diff(c)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    ag = float(np.mean(gain[:n]))
    al = float(np.mean(loss[:n]))
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(c)):
        ag = (ag * (n - 1) + gain[i - 1]) / n
        al = (al * (n - 1) + loss[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def stdev(x, n: int) -> np.ndarray:
    x = _as_float(x)
    out = np.full(x.shape, np.nan)
    if n <= 1 or len(x) < n:
        return np.zeros(x.shape) if n == 1 else out
    out[n - 1:] = _windows(x, n).std(axis=1, ddof=1)
    return out


def relative_volume(volume, n: int = 50) -> np.ndarray:
    """Today's volume / average volume of the previous n bars (excludes today)."""
    v = _as_float(volume)
    avg = sma(v, n)
    prev_avg = np.concatenate([[np.nan], avg[:-1]])
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(prev_avg > 0, v / prev_avg, np.nan)


def dollar_volume(close, volume, n: int = 20) -> np.ndarray:
    return sma(_as_float(close) * _as_float(volume), n)


def pct_change_n(x, n: int) -> np.ndarray:
    """Percent change from n bars ago to now (in percent)."""
    x = _as_float(x)
    out = np.full(x.shape, np.nan)
    if n <= 0 or len(x) <= n:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        out[n:] = (x[n:] / x[:-n] - 1.0) * 100.0
    return out


def distance_pct(x, ref) -> np.ndarray:
    x, ref = _as_float(x), _as_float(ref)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(ref > 0, (x / ref - 1.0) * 100.0, np.nan)


def linreg_slope(x, n: int) -> np.ndarray:
    """Slope of an OLS fit over the trailing n points, normalised by the mean."""
    x = _as_float(x)
    out = np.full(x.shape, np.nan)
    if n < 2 or len(x) < n:
        return out
    t = np.arange(n, dtype=float)
    tc = t - t.mean()
    denom = float(np.sum(tc * tc))
    w = _windows(x, n)
    m = w.mean(axis=1)
    num = (w - m[:, None]) @ tc
    with np.errstate(divide="ignore", invalid="ignore"):
        out[n - 1:] = np.where(m != 0, num / denom / m, np.nan)
    return out


def slope_r2(x, n: int) -> np.ndarray:
    """R-squared of a linear fit over the trailing n log-prices.

    This is the system's first candidate operationalisation of the qualitative
    concept "orderly / linear price movement". It is one candidate among
    several; see ``research.formalizer``.
    """
    x = _as_float(x)
    out = np.full(x.shape, np.nan)
    if n < 3:
        return out
    if len(x) < n:
        return out
    t = np.arange(n, dtype=float)
    tc = t - t.mean()
    stt = float(np.sum(tc * tc))
    with np.errstate(divide="ignore", invalid="ignore"):
        y = np.where(x > 0, np.log(np.maximum(x, 1e-12)), np.nan)
    w = _windows(y, n)
    yc = w - w.mean(axis=1, keepdims=True)
    syy = np.einsum("ij,ij->i", yc, yc)
    sty = yc @ tc
    with np.errstate(divide="ignore", invalid="ignore"):
        r2 = np.where(syy > 0, (sty * sty) / (stt * syy), 1.0)
    out[n - 1:] = r2
    return out


def rolling_apply(x, n: int, fn) -> np.ndarray:
    x = _as_float(x)
    out = np.full(x.shape, np.nan)
    for i in range(n - 1, len(x)):
        out[i] = fn(x[i - n + 1: i + 1])
    return out


def n_bars_above(x, ref, n: int) -> np.ndarray:
    """Count of the trailing n bars where x > ref."""
    x, ref = _as_float(x), _as_float(ref)
    ok = (x > ref).astype(float)
    ok[~np.isfinite(x) | ~np.isfinite(ref)] = np.nan
    return sma(ok, n) * n


def consecutive_true(mask) -> np.ndarray:
    mask = np.asarray(mask, dtype=bool)
    out = np.zeros(mask.shape, dtype=float)
    run = 0
    for i, m in enumerate(mask):
        run = run + 1 if m else 0
        out[i] = run
    return out


def zscore(x, n: int) -> np.ndarray:
    m, s = sma(x, n), stdev(x, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(s > 0, (_as_float(x) - m) / s, np.nan)
