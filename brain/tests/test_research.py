"""Research engine: analogs, hypothesis lab, formalizer, knowledge, regime, sectors."""

import datetime as dt

import numpy as np
import pytest

from tradingbrain.data.panel import build_panel
from tradingbrain.research.conflicts import KNOWN_CONFLICTS, conflict_report
from tradingbrain.research.formalizer import CONCEPTS, candidate_definitions, formalize
from tradingbrain.research.hypothesis import (SPLIT_FEATURES, HypothesisSpec, infer_spec)
from tradingbrain.research.knowledge import seed_knowledge_base, why_rule, knowledge_stats
from tradingbrain.stats.analogs import (AnalogQuery, analog_statistics,
                                        find_historical_setups)
from tradingbrain.strategy.spec import StrategySpec


def _permissive():
    return StrategySpec.from_dict({
        "name": "t", "version": "1",
        "universe": {"min_price": 1.0, "min_dollar_volume": 0.0},
        "market_filter": {"enabled": False},
        "setup": {"prior_move_min_pct": 20.0, "cons_max_range_ratio": 1.5,
                  "cons_max_volume_ratio": 1.5, "cons_require_ma_support": False,
                  "require_close_above_smas": []},
        "entry": {"breakout_min_rvol": 1.1},
    })


# ---------------------------------------------------------------- analogs
def test_analog_search_finds_the_designed_event(csv_hub):
    panel = build_panel(["BRKT"], csv_hub)
    q = AnalogQuery(spec=_permissive(), horizons=(1, 5, 10), target_r=2.0)
    events, meta = find_historical_setups(q, csv_hub, panel)
    assert len(events) >= 1
    e = events[0]
    assert e.symbol == "BRKT"
    assert e.risk_per_share > 0
    assert 10 in e.forward_returns


def test_analog_statistics_phrase_proportions_correctly(csv_hub):
    panel = build_panel(None, csv_hub)
    q = AnalogQuery(spec=_permissive(), horizons=(5, 10))
    events, meta = find_historical_setups(q, csv_hub, panel)
    st = analog_statistics(events, q, meta)
    assert st["n"] == len(events)
    for h in st["horizons"].values():
        assert "historical qualifying events" in h["statement"]
        assert "chance" not in h["statement"].lower()
        assert h["pct_positive_ci"][0] <= h["pct_positive"] <= h["pct_positive_ci"][1]
    for c in st["claims"]:
        assert c["evidence"] == "BACKTEST_RESULT"
        assert c["sample_size"] is not None


def test_analog_statistics_on_no_events_says_so():
    q = AnalogQuery(spec=_permissive())
    st = analog_statistics([], q, {"data_origin": "REAL", "data_provider": "test",
                                   "period": "x"})
    assert st["n"] == 0
    assert "No historical events matched" in st["claims"][0]["statement"]


def test_analog_excludes_the_symbol_under_analysis(csv_hub):
    panel = build_panel(None, csv_hub)
    q = AnalogQuery(spec=_permissive(), exclude_symbol="BRKT")
    events, _ = find_historical_setups(q, csv_hub, panel)
    assert all(e.symbol != "BRKT" for e in events)


# --------------------------------------------------------- hypothesis lab
def test_every_split_feature_declares_a_direction():
    for name, meta in SPLIT_FEATURES.items():
        assert "getter" in meta and "threshold" in meta and "question" in meta
        assert "hypothesised_better" in meta, f"{name} has no hypothesised direction"
        assert meta["hypothesised_better"] in ("above", "below", None)


def test_infer_spec_maps_questions_to_experiments():
    assert infer_spec("Does the market filter improve results?").kind == "filter"
    assert infer_spec("Compare definitions of tight consolidation").kind == "variants"
    s = infer_spec("Does volume contraction improve breakout expectancy?")
    assert s.kind == "split" and s.feature == "volume_contraction"


def test_infer_spec_admits_when_it_did_not_match():
    s = infer_spec("what is the airspeed velocity of an unladen swallow")
    assert "No specific experiment matched" in s.statement


def test_split_experiment_reports_direction(csv_hub):
    from tradingbrain.research.hypothesis import run_hypothesis_test
    from tradingbrain.db.store import Store
    import tempfile, pathlib
    store = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    panel = build_panel(None, csv_hub)
    spec = HypothesisSpec(kind="split", feature="volume_contraction", horizon=5,
                          strategy_key="sar_v1_0")
    r = run_hypothesis_test(spec, None, csv_hub, store, panel)
    assert r["verdict"] in ("SUPPORTED", "MIXED", "UNSUPPORTED", "INSUFFICIENT_DATA")
    assert "hypothesised_better_group" in r
    assert r["experiment_id"]
    assert store.experiment(r["experiment_id"]) is not None


# ----------------------------------------------------------- formalization
def test_every_concept_keeps_competing_definitions():
    for name, c in CONCEPTS.items():
        assert c["ambiguity"], f"{name} claims no ambiguity"
        assert len(c["candidates"]) >= 2, f"{name} has only one definition"
        for cand in c["candidates"]:
            assert cand["label"] and isinstance(cand["params"], dict)


def test_candidate_definitions_are_applicable_strategy_overrides():
    from tradingbrain.backtest.walkforward import _apply
    from tradingbrain.strategy.library import get_strategy
    base = get_strategy("sar_v1_0")
    for name, c in CONCEPTS.items():
        for cand in c["candidates"]:
            applied = _apply(base, cand["params"])
            assert applied.fingerprint() != "" , f"{name}/{cand['label']} failed to apply"


def test_formalize_refuses_to_invent_a_threshold():
    r = formalize("some concept nobody defined")
    assert not r["found"]
    assert "rather than inventing" in r["note"]


# ------------------------------------------------------------- knowledge
def test_knowledge_base_seeds_and_is_idempotent(tmp_store):
    a = seed_knowledge_base(tmp_store)
    b = seed_knowledge_base(tmp_store)
    assert a["knowledge_items"] > 15
    assert b["added"] == 0


def test_unverified_sources_store_no_quote(tmp_store):
    seed_knowledge_base(tmp_store)
    rows = tmp_store.knowledge(limit=1000)
    for r in rows:
        if not r["fulltext_verified"]:
            assert not r["quote"], (
                "a source whose page was never fetched must not carry a quotation")
    stats = knowledge_stats(tmp_store)
    assert stats["sources_unverified"] > 0
    assert "blocks outbound HTTPS" in stats["verification_note"]


def test_why_rule_returns_the_full_provenance_chain(tmp_store):
    seed_knowledge_base(tmp_store)
    r = why_rule("volume dry-up", tmp_store)
    assert r["found"]
    c = r["chains"][0]
    for step in ("step_1_source_says", "step_2_rule_implied",
                 "step_3_quantitative_candidates", "step_4_backtest_hypothesis"):
        assert c[step] is not None
    assert c["step_5_note"] or c["step_5_evidence"]


def test_why_rule_is_honest_about_unknown_concepts(tmp_store):
    seed_knowledge_base(tmp_store)
    r = why_rule("zzzz-not-a-concept", tmp_store)
    assert r["found"] is False or r["chains"]


# ------------------------------------------------------------- conflicts
def test_conflicts_are_preserved_not_merged(tmp_store):
    r = conflict_report(tmp_store)
    assert len(r["conflicts"]) >= 3
    for c in r["conflicts"]:
        assert c["side_a"]["position"] != c["side_b"]["position"]
        assert c["why_unresolved"]
        assert c["experiment"]
    assert "never merged" in r["principle"]


def test_conflict_strategy_arms_exist():
    from tradingbrain.strategy.library import LIBRARY
    for c in KNOWN_CONFLICTS:
        for side in ("side_a", "side_b"):
            assert c[side]["strategy_key"] in LIBRARY


# ---------------------------------------------------------------- regime
def test_regime_classifier_on_a_constructed_downtrend(csv_hub, tmp_path):
    from tradingbrain.data.providers.csv_cache import CsvCacheProvider
    from tradingbrain.regime.classifier import RegimeClassifier
    from tests.conftest import make_series
    down = make_series([100 * (0.995 ** i) for i in range(400)], symbol="SPY")
    CsvCacheProvider(csv_hub.settings.cache_dir).write(down)
    csv_hub._cache.clear()
    r = RegimeClassifier(csv_hub).classify(index_symbols=("SPY",), with_breadth=False)
    assert r.label in ("BEAR", "CORRECTION")
    assert r.filter_pass is False
    assert r.claim().evidence.value == "LIVE_MARKET_OBSERVATION"


def test_regime_handles_missing_index_data(csv_hub):
    from tradingbrain.regime.classifier import RegimeClassifier
    r = RegimeClassifier(csv_hub).classify(index_symbols=("NOPE",), with_breadth=False)
    assert r.label == "UNKNOWN"
    assert r.filter_pass is False


# --------------------------------------------------------------- sectors
def test_sector_ranking_uses_more_than_todays_gain(csv_hub):
    from tradingbrain.sectors.ranking import DEFAULT_WEIGHTS, SectorRanker
    r = SectorRanker(csv_hub, build_panel(None, csv_hub))
    ranks = r.rank({"A": ["BRKT"], "B": ["TEST"]}, "sector")
    assert len(ranks) == 2
    for g in ranks:
        assert set(g.components) == set(DEFAULT_WEIGHTS)
        assert g.rank in (1, 2)
    assert sum(DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)
