"""Setup-structure detectors."""

import numpy as np
import pytest

from tradingbrain.indicators.core import relative_volume, sma
from tradingbrain.indicators.structure import (BreakoutParams, ConsolidationParams,
                                               PriorMoveParams, detect_breakout,
                                               extension_metrics, find_consolidation,
                                               find_prior_move, scan_breakouts,
                                               scan_consolidations)


def test_prior_move_finds_the_advance(setup_series):
    s = setup_series
    pm = find_prior_move(s.high, s.low, 79, PriorMoveParams(lookback=63, min_pct=30))
    assert pm.qualifies
    assert pm.pct > 30
    assert 40 <= pm.low_idx <= 45          # the advance starts around bar 40


def test_prior_move_rejects_one_day_pops():
    from tests.conftest import make_series
    closes = [100.0] * 40 + [150.0] + [150.0] * 20
    s = make_series(closes)
    pm = find_prior_move(s.high, s.low, 55, PriorMoveParams(min_pct=30, min_bars=3))
    assert not pm.qualifies, "a single-bar gap must not count as a multi-day advance"


def test_consolidation_detected_with_contraction(setup_series):
    s = setup_series
    ma = sma(s.close, 20)
    base = find_consolidation(s.high, s.low, s.close, s.volume, 79,
                              ConsolidationParams(min_len=5, max_len=30), ma)
    assert base is not None
    assert base.qualifies
    assert base.volume_contraction < 1.0, "volume should dry up in this fixture"
    assert base.range_contraction < 1.0, "range should tighten in this fixture"
    assert base.depth_pct < 35


def test_breakout_triggers_on_the_designed_bar(setup_series):
    s = setup_series
    ma = sma(s.close, 20)
    rvol = relative_volume(s.volume, 50)
    sig = detect_breakout(s.high, s.low, s.close, s.volume, 80,
                          ConsolidationParams(min_len=5, max_len=30),
                          BreakoutParams(min_rvol=1.5), ma, rvol)
    assert sig.triggered, sig.reasons
    assert sig.rvol > 1.5
    assert s.close[80] > sig.breakout_level


def test_breakout_reasons_are_specific_when_it_fails(setup_series):
    s = setup_series
    ma = sma(s.close, 20)
    rvol = relative_volume(s.volume, 50)
    sig = detect_breakout(s.high, s.low, s.close, s.volume, 80,
                          ConsolidationParams(min_len=5, max_len=30),
                          BreakoutParams(min_rvol=99.0), ma, rvol)
    assert not sig.triggered
    assert any("relative volume" in r for r in sig.reasons)


def test_no_breakout_in_a_flat_market(flat):
    s = flat
    trig, _ = scan_breakouts(s.high, s.low, s.close, s.volume)
    assert trig.sum() == 0


def test_vectorised_scan_matches_the_scalar_detector(setup_series, ramp):
    for s in (setup_series, ramp):
        ma = sma(s.close, 20)
        p = ConsolidationParams()
        scan = scan_consolidations(s.high, s.low, s.close, s.volume, p, ma)
        for i in range(10, len(s)):
            a = find_consolidation(s.high, s.low, s.close, s.volume, i, p, ma)
            b = scan.at(i)
            assert (a is None) == (b is None), i
            if a is None:
                continue
            assert a.length == b.length, i
            assert a.qualifies == b.qualifies, i
            assert a.quality == pytest.approx(b.quality, abs=1e-9), i
            assert a.higher_low_ratio == pytest.approx(b.higher_low_ratio, abs=1e-12), i


def test_consolidation_scan_is_point_in_time(setup_series):
    s = setup_series
    full = scan_consolidations(s.high, s.low, s.close, s.volume, ma=sma(s.close, 20))
    for i in (70, 79, 95):
        t = s.upto(i)
        part = scan_consolidations(t.high, t.low, t.close, t.volume, ma=sma(t.close, 20))
        a, b = full.at(i), part.at(i)
        assert (a is None) == (b is None)
        if a:
            assert a.length == b.length
            assert a.quality == pytest.approx(b.quality, abs=1e-9)


def test_extension_metrics_disagree_by_construction(setup_series):
    m = extension_metrics(setup_series.close, setup_series.high, setup_series.low, 110)
    assert set(m) == {"pct_above_ma", "atr_above_ma", "adr_above_ma", "pct_from_20d_high"}
    assert np.isfinite(m["pct_above_ma"])
