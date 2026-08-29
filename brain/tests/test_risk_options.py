"""Risk sizing, safety guards, and options pricing."""

import math

import pytest

from tradingbrain.options.analysis import scenario_matrix
from tradingbrain.options.pricing import (black_scholes, break_even, greeks,
                                          implied_volatility)
from tradingbrain.risk.guards import TradingGuards
from tradingbrain.risk.sizing import (option_position_size, portfolio_check,
                                      position_size, risk_reward)


# ------------------------------------------------------------------ sizing
def test_position_size_basic_arithmetic():
    p = position_size(100_000, 1.0, 100.0, 95.0, max_position_pct=100.0)
    assert p.ok
    assert p.risk_per_share == pytest.approx(5.0)
    assert p.shares == 200            # $1000 risk / $5 per share
    assert p.actual_risk_dollars == pytest.approx(1000.0)
    assert p.position_value == pytest.approx(20_000.0)


def test_position_size_rejects_incoherent_inputs():
    assert not position_size(100_000, 1.0, 100.0, 105.0).ok        # long stop above entry
    assert not position_size(100_000, 1.0, 100.0, 95.0, direction="short").ok
    assert not position_size(0, 1.0, 100.0, 95.0).ok
    assert not position_size(100_000, 0, 100.0, 95.0).ok


def test_position_size_rounds_to_zero_rather_than_guessing():
    p = position_size(1_000, 0.1, 100.0, 50.0)
    assert not p.ok and "rounds to zero" in p.reason


def test_max_position_percent_caps_the_size():
    p = position_size(100_000, 5.0, 100.0, 99.0, max_position_pct=10.0)
    assert p.shares == 100            # 10% of 100k / $100
    assert any("max position" in c for c in p.constraints_applied)


def test_adr_stop_cap_warns_rather_than_silently_resizing():
    p = position_size(100_000, 1.0, 100.0, 95.0, adr_pct=2.0, max_stop_adr_multiple=1.0)
    assert p.ok
    assert any("ADR" in w for w in p.warnings)


def test_risk_reward_and_breakeven_win_rate():
    r = risk_reward(100, 95, 115)
    assert r["reward_risk"] == pytest.approx(3.0)
    assert r["breakeven_win_rate_pct"] == pytest.approx(25.0)
    assert not risk_reward(100, 100, 115)["ok"]


def test_portfolio_check_flags_breaches():
    r = portfolio_check([{"symbol": "A", "shares": 1000, "price": 100, "stop": 95}],
                        100_000)
    assert not r["ok"]
    assert r["exposure_pct"] == pytest.approx(100.0)
    assert r["open_risk_dollars"] == pytest.approx(5000.0)


def test_option_sizing_states_its_assumptions():
    r = option_position_size(100_000, 1.0, 3.50)
    assert r["ok"]
    assert r["max_loss"] == pytest.approx(r["total_cost"])
    assert any("premium paid" in a for a in r["assumptions"])
    assert any("short structures are NOT modelled" in a or "spread" in a
               for a in r["assumptions"])


# ------------------------------------------------------------------ guards
def test_live_trading_is_blocked_by_default():
    g = TradingGuards()
    assert g.status()["mode"] == "PAPER"
    assert g.status()["live_trading_enabled"] is False
    d = g.check_order({"symbol": "X", "shares": 1, "price": 1}, confirmed=True, live=True)
    assert not d["allowed"]
    assert any("live trading is disabled" in p for p in d["problems"])
    assert any("no broker adapter" in p for p in d["problems"])


def test_order_notional_cap_blocks_large_orders():
    g = TradingGuards()
    d = g.check_order({"symbol": "X", "shares": 10_000, "price": 100})
    assert not d["allowed"]
    assert any("notional" in p for p in d["problems"])


def test_daily_loss_limit_blocks_new_risk():
    g = TradingGuards(account_equity=100_000, realised_pnl_today=-4_000)
    d = g.check_order({"symbol": "X", "shares": 1, "price": 10})
    assert any("daily loss" in p for p in d["problems"])


# ----------------------------------------------------------------- pricing
def test_put_call_parity():
    s, k, t, r, sig = 100.0, 100.0, 0.5, 0.04, 0.30
    c = black_scholes(s, k, t, r, sig, "call")
    p = black_scholes(s, k, t, r, sig, "put")
    assert (c - p) == pytest.approx(s - k * math.exp(-r * t), abs=1e-9)


def test_option_value_is_monotone_in_volatility_and_time():
    base = black_scholes(100, 100, 0.5, 0.04, 0.3, "call")
    assert black_scholes(100, 100, 0.5, 0.04, 0.5, "call") > base
    assert black_scholes(100, 100, 1.0, 0.04, 0.3, "call") > base


def test_expiry_value_is_intrinsic():
    assert black_scholes(110, 100, 0, 0.04, 0.3, "call") == pytest.approx(10.0)
    assert black_scholes(90, 100, 0, 0.04, 0.3, "call") == pytest.approx(0.0)
    assert black_scholes(90, 100, 0, 0.04, 0.3, "put") == pytest.approx(10.0)


def test_greeks_have_the_right_signs_and_ranges():
    g = greeks(100, 100, 0.5, 0.04, 0.3, "call")
    assert 0 < g["delta"] < 1
    assert g["gamma"] > 0
    assert g["theta"] < 0            # long options decay
    assert g["vega"] > 0
    p = greeks(100, 100, 0.5, 0.04, 0.3, "put")
    assert -1 < p["delta"] < 0


def test_delta_approximates_the_price_derivative():
    g = greeks(100, 100, 0.5, 0.04, 0.3, "call")
    eps = 0.01
    fd = (black_scholes(100 + eps, 100, 0.5, 0.04, 0.3) -
          black_scholes(100 - eps, 100, 0.5, 0.04, 0.3)) / (2 * eps)
    assert g["delta"] == pytest.approx(fd, abs=1e-4)


def test_implied_volatility_round_trips():
    price = black_scholes(100, 105, 0.25, 0.04, 0.42, "call")
    iv = implied_volatility(price, 100, 105, 0.25, 0.04, "call")
    assert iv == pytest.approx(0.42, abs=1e-4)


def test_implied_volatility_returns_none_below_intrinsic():
    assert implied_volatility(0.01, 150, 100, 0.5, 0.04, "call") is None


def test_break_even():
    assert break_even(100, 5, "call") == 105
    assert break_even(100, 5, "put") == 95


def test_scenario_matrix_is_labelled_a_model_estimate():
    r = scenario_matrix(550, 570, 30, 12.5, 35.0)
    assert r["claim"]["evidence"] == "MODEL_ESTIMATE"
    assert any("THEORETICAL" in c for c in r["claim"]["caveats"])
    assert r["max_loss"] == pytest.approx(1250.0)
    # P/L must increase with the underlying for a call
    row = r["grid"][1]["rows"][0]["values"]
    pnls = [v["pnl_dollars"] for v in row]
    assert pnls == sorted(pnls)
