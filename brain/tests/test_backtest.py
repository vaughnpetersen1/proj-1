"""Backtest engine mechanics, verified on hand-built data.

These are the assertions that make the engine's output meaningful: fills happen
where the rules say they happen, gaps are honoured, costs are charged, and the
result does not change when future bars are added.
"""

import datetime as dt

import numpy as np
import pytest

from tradingbrain.backtest.engine import Backtester, run_backtest
from tradingbrain.backtest.metrics import compute_metrics, drawdown_series
from tradingbrain.backtest.montecarlo import MonteCarloConfig, run_monte_carlo
from tradingbrain.data.panel import build_panel
from tradingbrain.strategy.spec import StrategySpec


def _spec(**over):
    d = {
        "name": "test", "version": "1",
        "universe": {"min_price": 1.0, "min_dollar_volume": 0.0},
        "market_filter": {"enabled": False},
        "setup": {"prior_move_min_pct": 20.0, "prior_move_lookback": 63,
                  "cons_min_len": 5, "cons_max_len": 30,
                  "cons_max_range_ratio": 1.2, "cons_max_volume_ratio": 1.2,
                  "cons_require_ma_support": False,
                  "require_close_above_smas": []},
        "entry": {"breakout_min_rvol": 1.2, "breakout_require_gt_prev_volume": True,
                  "fill": "next_open"},
        "stop": {"type": "low_of_day", "max_stop_adr_multiple": None},
        "management": {"partials": [], "breakeven_after_r": None,
                       "trail": {"type": "sma", "length": 10, "basis": "close"},
                       "max_hold_days": 60},
        "risk": {"account_equity": 100000.0, "risk_pct": 1.0, "max_positions": 5},
        "costs": {"slippage_bps": 0.0, "commission_per_share": 0.0},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(d.get(k), dict):
            d[k].update(v)
        else:
            d[k] = v
    return StrategySpec.from_dict(d)


def test_engine_produces_a_trade_on_the_designed_breakout(csv_hub):
    panel = build_panel(["BRKT"], csv_hub)
    r = Backtester(_spec(), csv_hub, panel=panel, symbols=["BRKT"]).run()
    assert r.metrics["n_trades"] >= 1, r.signals_rejected
    t = r.trades[0]
    assert t.symbol == "BRKT"
    assert t.entry_price > 0 and t.initial_stop < t.entry_price


def test_fill_is_the_next_open_not_the_signal_close(csv_hub):
    panel = build_panel(["BRKT"], csv_hub)
    r = Backtester(_spec(), csv_hub, panel=panel, symbols=["BRKT"]).run()
    t = r.trades[0]
    series = csv_hub.daily("BRKT")
    idx = {d.date().isoformat(): i for i, d in enumerate(series.ts)}
    i = idx[t.entry_date]
    assert t.entry_price == pytest.approx(float(series.open[i]), rel=1e-9), \
        "entry must fill at the open of the bar AFTER the signal"
    assert t.initial_stop == pytest.approx(float(series.low[i - 1]), rel=1e-9), \
        "stop must be the low of the signal bar, which is bar i-1"


def test_slippage_and_commission_are_charged(csv_hub):
    panel = build_panel(["BRKT"], csv_hub)
    clean = Backtester(_spec(), csv_hub, panel=panel, symbols=["BRKT"]).run()
    costly = Backtester(_spec(costs={"slippage_bps": 50.0, "commission_per_share": 0.02}),
                        csv_hub, panel=panel, symbols=["BRKT"]).run()
    assert costly.trades[0].entry_price > clean.trades[0].entry_price
    assert costly.trades[0].commission > 0
    assert costly.metrics["expectancy_r"] < clean.metrics["expectancy_r"]


def test_gap_through_the_stop_fills_at_the_open(tmp_path):
    """A stop does not fill at the stop price when the market gaps past it."""
    from tradingbrain.data.providers.csv_cache import CsvCacheProvider
    from tradingbrain.data.providers import registry as reg
    from tradingbrain.config import Settings
    from tests.conftest import make_series

    # advance, tight base, breakout, then a catastrophic gap down.
    # The flat prefix is long enough to clear the engine's 60-bar warm-up.
    closes = [100.0] * 45
    px = 100.0
    for _ in range(15):
        px *= 1.02
        closes.append(px)
    top = px
    for i in range(10):
        closes.append(top * (1 - 0.01 * (i % 2)))
    closes.append(top * 1.05)          # breakout
    closes.append(top * 1.06)          # entry bar
    closes.append(top * 0.60)          # gap down far below any stop
    closes += [top * 0.6] * 10
    brk = 45 + 15 + 10                       # index of the breakout bar
    vols = [1e6] * brk + [4e6] + [1e6] * (len(closes) - brk - 1)
    s = make_series(closes, symbol="GAPD", volumes=vols, spread=0.002)

    # make_series opens every bar at the prior close, so a real overnight gap has
    # to be written in explicitly: the bar after entry opens far below the stop.
    gap = brk + 2
    floor = float(s.low[gap - 1]) * 0.70
    s.open[gap] = floor
    s.high[gap] = floor * 1.005
    s.low[gap] = floor * 0.97
    s.close[gap] = floor * 0.98
    for j in range(gap + 1, len(s)):
        s.open[j] = s.close[j - 1]
        s.close[j] = s.close[gap]
        s.high[j] = max(s.open[j], s.close[j]) * 1.002
        s.low[j] = min(s.open[j], s.close[j]) * 0.998

    cache = tmp_path / "c"
    CsvCacheProvider(cache).write(s)
    settings = Settings()
    settings.cache_dir = cache
    settings.provider_order = ("csv",)
    reg._providers.cache_clear(); reg.reset_availability_cache()
    old = reg.build_providers
    reg.build_providers = lambda _s=None: {"csv": CsvCacheProvider(cache)}
    try:
        hub = reg.DataHub(settings)
        panel = build_panel(["GAPD"], hub)
        r = Backtester(_spec(), hub, panel=panel, symbols=["GAPD"]).run()
        assert r.metrics["n_trades"] >= 1
        gapped = [t for t in r.trades if "gap" in (t.exit_reason or "")]
        assert gapped, f"expected a gap exit, got {[t.exit_reason for t in r.trades]}"
        t = gapped[0]
        assert t.avg_exit_price < t.initial_stop, \
            "a gap must fill below the stop, not at it"
        assert t.r_multiple < -1.0, "a gap loss must exceed 1R"
    finally:
        reg.build_providers = old
        reg._providers.cache_clear(); reg.reset_availability_cache()


def test_partials_reduce_size_and_are_recorded(csv_hub):
    panel = build_panel(["BRKT"], csv_hub)
    r = Backtester(_spec(management={"partials": [{"at_r": 1.0, "fraction": 0.5,
                                                   "after_days": None}]}),
                   csv_hub, panel=panel, symbols=["BRKT"]).run()
    t = r.trades[0]
    assert len(t.exits) >= 2, "a partial plus a final exit"
    assert any("partial" in f.reason for f in t.exits)
    assert sum(f.shares for f in t.exits) == t.shares


def test_risk_sizing_matches_the_specified_risk_percent(csv_hub):
    panel = build_panel(["BRKT"], csv_hub)
    r = Backtester(_spec(risk={"risk_pct": 0.5, "max_position_pct": 100.0}),
                   csv_hub, panel=panel, symbols=["BRKT"]).run()
    t = r.trades[0]
    budget = 100000.0 * 0.005
    assert t.risk_dollars <= budget + t.risk_per_share
    assert t.risk_dollars > budget - t.risk_per_share


def test_max_positions_is_respected(csv_hub):
    panel = build_panel(None, csv_hub)
    r = Backtester(_spec(risk={"max_positions": 1}), csv_hub, panel=panel).run()
    by_day = {}
    for t in r.trades:
        by_day.setdefault(t.entry_date, 0)
        by_day[t.entry_date] += 1
    assert all(v <= 1 for v in by_day.values())


def test_no_lookahead_trades_do_not_change_when_the_future_is_revealed(csv_hub):
    panel = build_panel(["BRKT"], csv_hub)
    series = csv_hub.daily("BRKT")
    cut = series.ts[95].date()
    early = Backtester(_spec(), csv_hub, end=cut, panel=panel, symbols=["BRKT"]).run()
    late = Backtester(_spec(), csv_hub, panel=panel, symbols=["BRKT"]).run()
    settled = series.ts[85].date().isoformat()

    def fingerprint(res):
        return [(t.symbol, t.entry_date, round(t.entry_price, 6), round(t.initial_stop, 6))
                for t in res.trades if t.entry_date <= settled]
    assert fingerprint(early) == fingerprint(late), \
        "trades established before the cut must be identical with and without future data"


def test_market_filter_removes_trades(csv_hub):
    panel = build_panel(None, csv_hub)
    off = Backtester(_spec(market_filter={"enabled": False}), csv_hub, panel=panel).run()
    on = Backtester(_spec(market_filter={"enabled": True, "symbol": "SPY",
                                         "secondary_symbol": "QQQ", "mode": "both"}),
                    csv_hub, panel=panel).run()
    assert on.metrics["n_trades"] <= off.metrics["n_trades"]
    assert "market filter" in on.signals_rejected or on.metrics["n_trades"] == off.metrics["n_trades"]


def test_result_carries_survivorship_and_origin_warnings(csv_hub):
    panel = build_panel(["BRKT"], csv_hub)
    r = Backtester(_spec(), csv_hub, panel=panel, symbols=["BRKT"]).run()
    assert any("survivorship" in w.lower() or "delisted" in w.lower() for w in r.warnings)
    c = r.claim()
    assert c.evidence.value == "BACKTEST_RESULT"
    assert c.data_source


def test_metrics_on_a_known_trade_list():
    class T:
        def __init__(self, pnl, r, bars=5):
            self.pnl, self.r_multiple, self.bars_held = pnl, r, bars
            self.commission, self.mae_r, self.mfe_r = 0.0, -0.5, 2.0
    trades = [T(100, 1.0), T(-50, -0.5), T(200, 2.0), T(-100, -1.0)]
    curve = [{"date": "2020-01-01", "equity": 100000, "exposure_pct": 10},
             {"date": "2020-01-02", "equity": 100100, "exposure_pct": 10},
             {"date": "2020-01-03", "equity": 100050, "exposure_pct": 10},
             {"date": "2020-01-04", "equity": 100150, "exposure_pct": 10}]
    m = compute_metrics(trades, curve, 100000)
    assert m["n_trades"] == 4
    assert m["win_rate_pct"] == 50.0
    assert m["expectancy_dollars"] == pytest.approx(37.5)
    assert m["expectancy_r"] == pytest.approx(0.375)
    assert m["profit_factor"] == pytest.approx(300 / 150)
    assert m["max_consecutive_losses"] == 1


def test_metrics_flag_small_samples():
    class T:
        pnl, r_multiple, bars_held, commission, mae_r, mfe_r = 1.0, 1.0, 1, 0.0, 0.0, 1.0
    m = compute_metrics([T()] * 5, [{"date": "d", "equity": 1, "exposure_pct": 0}], 1)
    assert any("SMALL SAMPLE" in n for n in m["notes"])


def test_metrics_report_no_trades_clearly():
    m = compute_metrics([], [{"date": "d", "equity": 100, "exposure_pct": 0}], 100)
    assert m["n_trades"] == 0
    assert any("NO TRADES" in n for n in m["notes"])


def test_drawdown_series_is_never_positive():
    eq = np.array([100.0, 110.0, 90.0, 120.0])
    dd = drawdown_series(eq)
    assert dd.max() <= 0
    assert dd.min() == pytest.approx((90 / 110 - 1) * 100)


def test_monte_carlo_refuses_tiny_samples():
    r = run_monte_carlo([0.1, -0.2, 0.3])
    assert not r["ok"] and "10" in r["reason"]


def test_monte_carlo_percentiles_are_ordered():
    rng = np.random.default_rng(5)
    rs = list(rng.normal(0.2, 1.0, 200))
    r = run_monte_carlo(rs, MonteCarloConfig(n_simulations=500))
    assert r["ok"]
    tr = r["total_return_pct"]
    assert tr["p05"] <= tr["p25"] <= tr["p50"] <= tr["p75"] <= tr["p95"]
    assert r["max_drawdown_pct"]["worst"] <= r["max_drawdown_pct"]["p05"]
    assert r["claim"]["evidence"] == "MODEL_ESTIMATE"
    assert any("not a prediction" in c for c in r["claim"]["caveats"])


def test_monte_carlo_shuffle_preserves_the_multiset():
    rs = [1.0, -1.0, 2.0, -0.5] * 30
    r = run_monte_carlo(rs, MonteCarloConfig(n_simulations=200, method="shuffle"))
    assert r["ok"]
    assert "path dependence" in " ".join(r["assumptions"])
