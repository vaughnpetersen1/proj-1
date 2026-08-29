"""End-to-end integration over the generated universe.

Marked ``slow`` because these run real backtests. They are the tests that would
catch an engine that runs but produces nonsense: they assert internal
consistency (equity change equals summed P/L, R multiples reconcile with the
recorded risk) rather than particular returns, which are properties of the data,
not of the code.

Run with:  pytest -m slow
"""

import datetime as dt

import numpy as np
import pytest

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def panel():
    from tradingbrain.data.panel import build_panel
    return build_panel(max_symbols=40)


@pytest.fixture(scope="module")
def result(panel):
    from tradingbrain.backtest.engine import Backtester
    from tradingbrain.strategy.library import get_strategy
    return Backtester(get_strategy("sar_v1_1_adr_stop"), start=dt.date(2016, 1, 1),
                      panel=panel).run()


def test_backtest_runs_and_reports_provenance(result):
    assert result.metrics["n_trades"] >= 0
    assert result.data_origin in ("SYNTHETIC", "REAL", "MIXED")
    assert result.warnings, "a result must always disclose its known biases"
    c = result.claim()
    assert c.evidence.value == "BACKTEST_RESULT"
    assert c.sample_size == result.metrics["n_trades"]


def test_equity_change_reconciles_with_trade_pnl(result):
    if not result.trades:
        pytest.skip("no trades produced")
    start = result.metrics["starting_equity"]
    end = result.metrics["ending_equity"]
    total = sum(t.pnl for t in result.trades)
    assert (end - start) == pytest.approx(total, rel=1e-6, abs=1.0), \
        "ending equity must equal starting equity plus the sum of realised P/L"


def test_r_multiples_reconcile_with_recorded_risk(result):
    for t in result.trades[:200]:
        assert t.r_multiple == pytest.approx(t.pnl / t.risk_dollars, rel=1e-9)
        assert t.risk_dollars == pytest.approx(t.risk_per_share * t.shares, rel=1e-9)


def test_every_trade_exits_and_shares_reconcile(result):
    for t in result.trades:
        assert t.exit_date is not None
        assert sum(f.shares for f in t.exits) == t.shares
        assert t.entry_date <= t.exit_date


def test_stops_are_below_entries_for_longs(result):
    for t in result.trades:
        assert t.initial_stop < t.entry_price


def test_no_lookahead_over_the_full_universe(panel):
    """Trades settled before a cut date must be identical with and without future data."""
    from tradingbrain.backtest.engine import Backtester
    from tradingbrain.strategy.library import get_strategy
    spec = get_strategy("sar_v1_0")
    cut = dt.date(2020, 1, 1)
    early = Backtester(spec, start=dt.date(2016, 1, 1), end=cut, panel=panel).run()
    late = Backtester(spec, start=dt.date(2016, 1, 1), panel=panel).run()
    settled = "2019-06-01"

    def fp(res):
        return [(t.symbol, t.entry_date, round(t.entry_price, 6), round(t.r_multiple, 6))
                for t in res.trades if t.exit_date and t.exit_date < settled]
    assert fp(early) == fp(late)


def test_market_filter_arm_comparison_is_coherent(panel):
    from tradingbrain.backtest.engine import Backtester
    from tradingbrain.strategy.library import get_strategy
    on = Backtester(get_strategy("sar_v1_0"), start=dt.date(2016, 1, 1), panel=panel).run()
    off = Backtester(get_strategy("sar_no_market_filter_v1_0"), start=dt.date(2016, 1, 1),
                     panel=panel).run()
    assert off.metrics["n_trades"] >= on.metrics["n_trades"], \
        "removing a filter cannot reduce the number of candidates"


def test_walk_forward_reports_degradation(panel):
    from tradingbrain.backtest.walkforward import run_walk_forward
    from tradingbrain.strategy.library import get_strategy
    r = run_walk_forward(get_strategy("sar_v1_0"),
                         {"setup.prior_move_min_pct": [20, 30, 40]},
                         dt.date(2013, 1, 1), dt.date(2026, 8, 28),
                         train_years=4, test_years=2, panel=panel, min_trades=5)
    assert r["ok"]
    assert r["verdict"] in ("OUT_OF_SAMPLE_HOLDS", "SUBSTANTIAL_DEGRADATION",
                            "FAILS_OUT_OF_SAMPLE", "INSUFFICIENT_DATA")
    assert r["parameter_stability"]
    assert any("test window is never used to choose" in c
               for c in r["claim"]["caveats"])
    for fold in r["folds"]:
        if fold.get("selected"):
            assert fold["window"]["train_end"] < fold["window"]["test_start"], \
                "the test window must start after training ends"


def test_sensitivity_sweep_labels_fragility(panel):
    from tradingbrain.backtest.sensitivity import run_sensitivity
    from tradingbrain.strategy.library import get_strategy
    r = run_sensitivity(get_strategy("sar_v1_0"),
                        {"entry.breakout_min_rvol": [1.0, 1.5, 2.0]},
                        dt.date(2018, 1, 1), dt.date(2026, 8, 28), panel=panel)
    assert r["overall_verdict"] in ("FRAGILE", "ROBUST_ACROSS_SWEPT_PARAMETERS", "NOT_TESTED")
    sweep = r["sweeps"]["entry.breakout_min_rvol"]
    assert len(sweep["points"]) == 3
    assert any("interaction" in c for c in r["claim"]["caveats"])


def test_scanner_and_analyzer_agree_on_qualification(panel):
    """The scanner and the analyzer must not disagree about whether a setup qualifies."""
    from tradingbrain.ai.analyzer import TradeIdea, analyze_trade
    from tradingbrain.scanner.scan import ScanConfig, run_scan
    scan = run_scan(ScanConfig(limit=5, strategy_key="sar_v1_1_adr_stop"), panel=panel)
    if not scan.get("results"):
        pytest.skip("no candidates today")
    top = scan["results"][0]
    a = analyze_trade(TradeIdea(symbol=top["symbol"], strategy_key="sar_v1_1_adr_stop"),
                      panel=panel, with_analogs=False)
    assert a["ok"]
    assert a["setup"]["matches_strategy"] is True, \
        "a scanner candidate must also pass the analyzer's evaluation"


def test_demo_command_exercises_every_engine():
    from tradingbrain.cli import demo
    r = demo()
    failed = [s for s in r["steps"] if not s["ok"]]
    assert not failed, failed
    assert r["ok"]
