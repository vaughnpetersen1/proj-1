"""Indicator correctness and the point-in-time invariant.

The invariant -- element i is computed only from elements <= i -- is what makes
it safe for the backtester to read indicator[i] while standing on bar i. It is
asserted directly by comparing the full-series value at i against the value
computed from a series truncated at i.
"""

import numpy as np
import pytest

from tradingbrain.indicators.core import (adr_pct, atr, dollar_volume, ema, linreg_slope,
                                          pct_change_n, relative_volume, rolling_max,
                                          rolling_min, rsi, sma, slope_r2, stdev,
                                          true_range, zscore)

FNS_1ARG = [(sma, 20), (ema, 20), (rolling_max, 20), (rolling_min, 20), (stdev, 20),
            (rsi, 14), (relative_volume, 50), (slope_r2, 20), (linreg_slope, 20),
            (pct_change_n, 21), (zscore, 20)]


def test_sma_known_values():
    x = np.arange(1, 11, dtype=float)
    out = sma(x, 3)
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert out[2] == pytest.approx(2.0)
    assert out[9] == pytest.approx(9.0)


def test_ema_seeds_with_sma():
    x = np.arange(1, 21, dtype=float)
    out = ema(x, 5)
    assert out[4] == pytest.approx(3.0)          # mean of 1..5
    assert out[5] == pytest.approx(6 * (2 / 6) + 3.0 * (4 / 6))


def test_true_range_and_atr():
    high = np.array([10.0, 11.0, 12.0])
    low = np.array([9.0, 9.5, 11.0])
    close = np.array([9.5, 10.5, 11.5])
    tr = true_range(high, low, close)
    assert tr[0] == pytest.approx(1.0)
    assert tr[1] == pytest.approx(1.5)           # high - prev close
    assert np.isnan(atr(high, low, close, 14)).all()


def test_adr_is_high_over_low_not_atr():
    high = np.array([110.0] * 25)
    low = np.array([100.0] * 25)
    out = adr_pct(high, low, 20)
    assert out[24] == pytest.approx(10.0)        # 110/100 - 1 = 10%


def test_relative_volume_excludes_today():
    v = np.array([100.0] * 50 + [500.0])
    rv = relative_volume(v, 50)
    assert rv[50] == pytest.approx(5.0)


def test_slope_r2_is_one_on_a_perfect_exponential():
    x = np.array([100 * 1.01 ** i for i in range(40)])
    assert slope_r2(x, 20)[-1] == pytest.approx(1.0, abs=1e-9)


def test_slope_r2_low_on_noise():
    rng = np.random.default_rng(7)
    x = 100 + rng.standard_normal(200).cumsum() * 0.01
    vals = slope_r2(x, 20)
    assert np.nanmean(vals) < 0.95


def test_rsi_bounds_and_all_up_series():
    x = np.arange(1, 60, dtype=float)
    out = rsi(x, 14)
    assert out[20] == pytest.approx(100.0)
    finite = out[np.isfinite(out)]
    assert finite.min() >= 0 and finite.max() <= 100


def test_dollar_volume():
    c = np.array([10.0] * 25)
    v = np.array([1000.0] * 25)
    assert dollar_volume(c, v, 20)[24] == pytest.approx(10_000.0)


@pytest.mark.parametrize("fn,n", FNS_1ARG)
def test_point_in_time_invariant(fn, n, ramp):
    """The value at index i must not change when future bars are removed."""
    x = ramp.close if fn is not relative_volume else ramp.volume
    full = fn(x, n)
    for i in (60, 80, 100, len(x) - 1):
        truncated = fn(x[: i + 1], n)
        a, b = full[i], truncated[-1]
        assert (np.isnan(a) and np.isnan(b)) or a == pytest.approx(b, rel=1e-12, abs=1e-12)


def test_atr_and_adr_point_in_time(ramp):
    a_full = atr(ramp.high, ramp.low, ramp.close, 14)
    d_full = adr_pct(ramp.high, ramp.low, 20)
    for i in (40, 70, 110):
        a_t = atr(ramp.high[: i + 1], ramp.low[: i + 1], ramp.close[: i + 1], 14)[-1]
        d_t = adr_pct(ramp.high[: i + 1], ramp.low[: i + 1], 20)[-1]
        assert a_full[i] == pytest.approx(a_t, rel=1e-12)
        assert d_full[i] == pytest.approx(d_t, rel=1e-12)


def test_warmup_is_nan_not_zero():
    x = np.arange(50, dtype=float)
    assert np.isnan(sma(x, 20)[:19]).all()
    assert np.isnan(atr(x + 1, x, x, 14)[:13]).all()


def test_short_input_does_not_crash():
    x = np.array([1.0, 2.0])
    for fn, n in FNS_1ARG:
        out = fn(x, 20)
        assert len(out) == 2
