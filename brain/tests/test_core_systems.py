"""Provenance, spec validation, data quality, regime, store, tools and API."""

import datetime as dt
import json

import numpy as np
import pytest

from tradingbrain.data.quality import validate
from tradingbrain.provenance import (Claim, DataOrigin, EvidenceClass, proportion_statement)
from tradingbrain.strategy.spec import SpecError, StrategySpec


# ------------------------------------------------------------- provenance
def test_claim_requires_a_data_source_for_empirical_classes():
    with pytest.raises(ValueError):
        Claim("x", EvidenceClass.BACKTEST_RESULT)
    with pytest.raises(ValueError):
        Claim("x", EvidenceClass.LIVE_MARKET_OBSERVATION)
    Claim("x", EvidenceClass.HYPOTHESIS)          # no source needed


def test_synthetic_origin_adds_an_unremovable_caveat():
    c = Claim("x", EvidenceClass.BACKTEST_RESULT, data_source="gen",
              data_origin=DataOrigin.SYNTHETIC)
    assert any("SYNTHETIC" in cv for cv in c.caveats)


def test_proportion_statement_never_says_chance():
    s = proportion_statement(271, 428, "10 trading days", "were positive")
    assert "428" in s and "63.3%" in s
    assert "chance" not in s.lower()
    assert proportion_statement(0, 0, "h", "c").startswith("No qualifying events")


def test_claim_render_carries_every_qualifier():
    c = Claim("edge exists", EvidenceClass.BACKTEST_RESULT, sample_size=100,
              period="2015..2020", data_source="csv", data_origin=DataOrigin.REAL,
              confidence_interval=(0.1, 0.3), methodology="bootstrap")
    r = c.render()
    for token in ("BACKTEST_RESULT", "N = 100", "95% CI", "2015..2020", "csv", "bootstrap"):
        assert token in r


# ---------------------------------------------------------- strategy spec
def test_spec_round_trips_and_fingerprints_stably():
    s = StrategySpec()
    assert StrategySpec.from_dict(s.to_dict()).fingerprint() == s.fingerprint()


def test_spec_rejects_unknown_fields():
    with pytest.raises(SpecError):
        StrategySpec.from_dict({"setup": {"not_a_field": 1}})
    with pytest.raises(SpecError):
        StrategySpec.from_dict({"nope": 1})


def test_spec_rejects_incoherent_values():
    for bad in ({"risk": {"risk_pct": 90}},
                {"setup": {"cons_min_len": 30, "cons_max_len": 5}},
                {"entry": {"breakout_min_close_position": 3.0}},
                {"direction": "sideways"},
                {"management": {"partials": [{"at_r": 1, "fraction": 0.8},
                                             {"at_r": 2, "fraction": 0.8}]}}):
        with pytest.raises(SpecError):
            StrategySpec.from_dict(bad)


def test_bumping_a_version_does_not_mutate_the_original():
    a = StrategySpec()
    b = a.bumped("9.9", description="changed")
    assert a.version != b.version
    assert a.description != b.description


def test_strategy_library_entries_all_validate_and_cite_sources():
    from tradingbrain.strategy.library import LIBRARY, get_strategy
    assert len(LIBRARY) >= 5
    for key in LIBRARY:
        s = get_strategy(key)
        s.validate()
        assert s.description
        if not key.startswith("sar_no_market_filter"):
            assert s.provenance, f"{key} has no provenance"
        for p in s.provenance:
            assert p["sources"] and p["claim"] and p["interpretation"]


# ------------------------------------------------------------ data quality
def test_quality_detects_duplicate_timestamps(ramp):
    s = ramp
    s.ts[10] = s.ts[9]
    rep = validate(s)
    assert not rep.ok
    assert any(i.code == "duplicate_timestamps" for i in rep.issues)


def test_quality_detects_ohlc_inconsistency(ramp):
    ramp.high[5] = ramp.low[5] - 1
    rep = validate(ramp)
    assert not rep.ok
    assert any(i.code == "ohlc_inconsistent" for i in rep.issues)


def test_quality_detects_an_unadjusted_split(ramp):
    ramp.close[50] = ramp.close[49] / 4        # 4:1 split, unadjusted
    ramp.high[50] = ramp.close[50] * 1.01
    ramp.low[50] = ramp.close[50] * 0.99
    ramp.open[50] = ramp.close[50]
    rep = validate(ramp)
    assert any(i.code == "possible_unadjusted_corporate_action" for i in rep.issues)


def test_quality_flags_unadjusted_series(ramp):
    ramp.adjusted = False
    assert any(i.code == "unadjusted" for i in validate(ramp).issues)


def test_quality_flags_synthetic_origin(ramp):
    ramp.origin = DataOrigin.SYNTHETIC
    assert any(i.code == "synthetic" for i in validate(ramp).issues)


def test_empty_series_is_an_error():
    from tradingbrain.data.types import PriceSeries, Timeframe
    s = PriceSeries("X", Timeframe.D1, [], np.array([]), np.array([]), np.array([]),
                    np.array([]), np.array([]))
    assert not validate(s).ok


# ------------------------------------------------------------ point in time
def test_price_series_upto_truncates(ramp):
    t = ramp.upto(10)
    assert len(t) == 11
    assert t.close[-1] == ramp.close[10]


def test_index_of_never_returns_a_future_bar(ramp):
    d = ramp.ts[20].date()
    assert ramp.index_of(d) == 20
    assert ramp.index_of(dt.date(1990, 1, 1)) == -1


# ------------------------------------------------------------------- store
def test_store_round_trips_research_artifacts(tmp_store):
    sid = tmp_store.add_source(source="S", url="u", title="t")
    kid = tmp_store.add_knowledge(source_id=sid, category="SETUP", concept="c",
                                  observation="o", explicit=True,
                                  quantitative_interpretation=[{"a": 1}])
    rows = tmp_store.knowledge()
    assert len(rows) == 1 and rows[0]["quantitative_interpretation"] == [{"a": 1}]
    hid = tmp_store.add_hypothesis("q?", "s")
    eid = tmp_store.add_experiment(hypothesis_id=hid, name="e", spec={"k": 1},
                                   results={"r": 2}, verdict="SUPPORTED", sample_size=10)
    assert tmp_store.experiment(eid)["results"] == {"r": 2}
    assert tmp_store.hypotheses()[0]["status"] == "TESTED"


def test_strategy_versions_are_immutable(tmp_store):
    a = tmp_store.save_strategy("S", "1.0", {"x": 1})
    b = tmp_store.save_strategy("S", "1.0", {"x": 2})
    assert a == b
    assert tmp_store.strategy(a)["spec"] == {"x": 1}, "an existing version must not be overwritten"


def test_journal_round_trip_and_derivations(tmp_store):
    from tradingbrain.journal.analytics import import_trades_csv, journal_summary
    csv = ("ticker,entry_date,entry_price,stop_price,position_size,exit_date,exit_price\n"
           "AAA,2024-01-02,100,95,100,2024-01-10,120\n"
           "BBB,2024-02-02,50,48,200,2024-02-05,44\n")
    r = import_trades_csv(csv, tmp_store)
    assert r["imported"] == 2 and not r["errors"]
    trades = tmp_store.trades()
    a = next(t for t in trades if t["ticker"] == "AAA")
    assert a["pnl"] == pytest.approx(2000.0)
    assert a["r_multiple"] == pytest.approx(4.0)
    s = journal_summary(tmp_store)
    assert s["closed"] == 2 and s["win_rate_pct"] == 50.0


def test_journal_analysis_refuses_to_claim_patterns_from_nothing(tmp_store):
    from tradingbrain.journal.analytics import analyze_trading_performance
    r = analyze_trading_performance(tmp_store)
    assert r["findings"][0]["confidence"] == "NONE"
    assert "Not enough closed trades" in r["findings"][0]["finding"]


# ------------------------------------------------------------------- tools
def test_every_tool_is_introspectable():
    from tradingbrain.ai.tools import TOOLS, tool_catalog
    cat = tool_catalog()
    assert len(cat) == len(TOOLS) >= 30
    for t in cat:
        assert t["name"] in TOOLS
        assert isinstance(t["parameters"], list)


def test_call_tool_reports_unknown_names_and_bad_arguments():
    from tradingbrain.ai.tools import call_tool
    assert "unknown tool" in call_tool("nope")["error"]
    r = call_tool("get_market_data", not_a_param=1)
    assert "does not accept" in r["error"]


def test_call_tool_turns_exceptions_into_data():
    from tradingbrain.ai.tools import call_tool
    r = call_tool("get_market_data", symbol="___NOT_A_SYMBOL___")
    assert "error" in r


def test_news_tool_admits_it_has_no_provider():
    from tradingbrain.ai.tools import get_news
    r = get_news("NVDA")
    assert r["available"] is False and r["articles"] == []
    assert "episodic" in r["impact"].lower()


# ----------------------------------------------------------------- routing
def test_router_picks_sensible_intents():
    from tradingbrain.ai.orchestrator import Router
    r = Router()
    cases = {
        "What is the market regime?": "regime",
        "What sectors are leading?": "sectors",
        "What mistakes have I been making?": "journal",
        "Find today's best setups": "scan",
        "Backtest the SAR strategy": "backtest",
        "Run a monte carlo": "montecarlo",
    }
    for q, expected in cases.items():
        _, kind = r.plan(q)
        assert kind == expected, f"{q!r} routed to {kind}, expected {expected}"


def test_router_does_not_mistake_english_words_for_tickers():
    from tradingbrain.ai.orchestrator import _extract_symbol
    assert _extract_symbol("are all of these good?") is None
    assert _extract_symbol("Analyze NVDA please") == "NVDA"


# --------------------------------------------------------------------- api
@pytest.fixture(scope="module")
def server():
    import threading
    from tradingbrain.api.app import create_server
    srv = create_server("127.0.0.1", 8799)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:8799"
    srv.shutdown()


@pytest.mark.parametrize("path", [
    "/", "/styles.css", "/js/app.js", "/api/status", "/api/providers",
    "/api/regime?breadth=false", "/api/knowledge", "/api/concepts", "/api/conflicts",
    "/api/research", "/api/strategies", "/api/journal", "/api/alerts", "/api/tools",
    "/api/ai/modes", "/api/risk/guards",
])
def test_api_routes_respond(server, path):
    import urllib.request
    with urllib.request.urlopen(server + path, timeout=120) as r:
        assert r.status == 200
        assert len(r.read()) > 0


def test_api_unknown_route_is_a_clean_404(server):
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen(server + "/api/does-not-exist", timeout=30)
        assert False, "expected 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404
        assert "no route" in json.loads(e.read())["error"]


def test_api_rejects_malformed_json(server):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(server + "/api/analyze", data=b"{not json",
                                 headers={"content-type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30)
        assert False, "expected 400"
    except urllib.error.HTTPError as e:
        assert e.code == 400
