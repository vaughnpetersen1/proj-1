"""Rule formalisation: qualitative concept -> competing measurable definitions.

The rule the brief insists on: do not pick one operationalisation and pretend it
is objectively correct. Record the concept, enumerate candidates, test them,
compare, and report whether the concept survives the choice of definition.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from ..db.store import STORE, Store

#: concept -> (category, description, ambiguity, [candidate definitions])
#: Each candidate is a dotted StrategySpec override that the Hypothesis Lab can
#: apply directly, so a definition is never "described" without being runnable.
CONCEPTS: dict[str, dict[str, Any]] = {
    "strong prior move": {
        "category": "SETUP",
        "description": "A large price expansion preceding the base.",
        "ambiguity": ("Sources say '30%+ over days to weeks' and '30-100%+ in 1-3 months'. "
                      "These differ in both magnitude and window and cannot be reconciled "
                      "into one number without choosing."),
        "candidates": [
            {"label": "+20% in 10 bars", "params": {"setup.prior_move_min_pct": 20,
                                                    "setup.prior_move_max_bars": 10}},
            {"label": "+30% in 20 bars", "params": {"setup.prior_move_min_pct": 30,
                                                    "setup.prior_move_max_bars": 20}},
            {"label": "+30% in 60 bars", "params": {"setup.prior_move_min_pct": 30,
                                                    "setup.prior_move_max_bars": 60}},
            {"label": "+50% in 30 bars", "params": {"setup.prior_move_min_pct": 50,
                                                    "setup.prior_move_max_bars": 30}},
            {"label": "+100% in 63 bars", "params": {"setup.prior_move_min_pct": 100,
                                                     "setup.prior_move_max_bars": 63}},
            {"label": "no prior-move requirement", "params": {"setup.prior_move_min_pct": 0}},
        ],
    },
    "tight consolidation": {
        "category": "SETUP",
        "description": "A base whose range contracts as it forms.",
        "ambiguity": ("'Tight' is described visually. Range contraction, absolute depth and "
                      "average daily range are three different measurements that rank the "
                      "same bases differently."),
        "candidates": [
            {"label": "2nd-half range <= 0.7x 1st-half",
             "params": {"setup.cons_max_range_ratio": 0.7}},
            {"label": "2nd-half range <= 1.0x 1st-half",
             "params": {"setup.cons_max_range_ratio": 1.0}},
            {"label": "2nd-half range <= 1.2x 1st-half",
             "params": {"setup.cons_max_range_ratio": 1.2}},
            {"label": "depth <= 15% (shallow base instead of contracting)",
             "params": {"setup.cons_max_depth_pct": 15.0,
                        "setup.cons_max_range_ratio": 99.0}},
            {"label": "depth <= 25%", "params": {"setup.cons_max_depth_pct": 25.0,
                                                 "setup.cons_max_range_ratio": 99.0}},
        ],
    },
    "volume dry-up": {
        "category": "SETUP",
        "description": "Volume declining while the base forms.",
        "ambiguity": "Contraction can be measured half-vs-half, against the expansion leg, "
                     "or as a trend slope. Half-vs-half is used here.",
        "candidates": [
            {"label": "2nd-half volume <= 0.7x 1st-half",
             "params": {"setup.cons_max_volume_ratio": 0.7}},
            {"label": "2nd-half volume <= 1.0x 1st-half",
             "params": {"setup.cons_max_volume_ratio": 1.0}},
            {"label": "no volume condition", "params": {"setup.cons_max_volume_ratio": 99.0}},
        ],
    },
    "high-volume breakout": {
        "category": "ENTRY",
        "description": "Volume expansion confirming the range break.",
        "ambiguity": "'High volume' has no stated multiple in the sources.",
        "candidates": [
            {"label": "rvol >= 1.0 (none)", "params": {"entry.breakout_min_rvol": 1.0}},
            {"label": "rvol >= 1.5", "params": {"entry.breakout_min_rvol": 1.5}},
            {"label": "rvol >= 2.0", "params": {"entry.breakout_min_rvol": 2.0}},
            {"label": "rvol >= 3.0", "params": {"entry.breakout_min_rvol": 3.0}},
        ],
    },
    "extended": {
        "category": "STOCK_SELECTION",
        "description": "Price too far from its trend to buy.",
        "ambiguity": ("Percent above a moving average, ATR units and ADR units disagree "
                      "systematically: a 3% move is extended for a low-ADR name and normal "
                      "for a high-ADR one."),
        "candidates": [
            {"label": "<= 10% above the 20 SMA", "params": {"setup.max_pct_above_sma20": 10}},
            {"label": "<= 20% above the 20 SMA", "params": {"setup.max_pct_above_sma20": 20}},
            {"label": "<= 3 ADRs above the 20 SMA",
             "params": {"setup.max_adr_above_sma20": 3.0}},
            {"label": "<= 5 ADRs above the 20 SMA",
             "params": {"setup.max_adr_above_sma20": 5.0}},
            {"label": "no extension filter", "params": {"setup.max_pct_above_sma20": None,
                                                        "setup.max_adr_above_sma20": None}},
        ],
    },
    "orderly price action": {
        "category": "SETUP",
        "description": "A smooth, linear advance rather than an erratic one.",
        "ambiguity": "No measurement is given anywhere in the sources.",
        "candidates": [
            {"label": "R^2 of 20-bar log-price fit >= 0.5", "params": {"setup.min_r2_20": 0.5}},
            {"label": "R^2 >= 0.7", "params": {"setup.min_r2_20": 0.7}},
            {"label": "R^2 >= 0.85", "params": {"setup.min_r2_20": 0.85}},
            {"label": "no orderliness filter", "params": {"setup.min_r2_20": None}},
        ],
    },
    "leading stock": {
        "category": "STOCK_SELECTION",
        "description": "A stock outperforming its peers.",
        "ambiguity": "Leadership horizon is unspecified; 1-month and 6-month leaders are "
                     "often different names.",
        "candidates": [
            {"label": "top 20% by 63-bar return",
             "params": {"setup.min_rs_percentile": 80, "setup.rs_lookback": 63}},
            {"label": "top 10% by 63-bar return",
             "params": {"setup.min_rs_percentile": 90, "setup.rs_lookback": 63}},
            {"label": "top 20% by 21-bar return",
             "params": {"setup.min_rs_percentile": 80, "setup.rs_lookback": 21}},
            {"label": "top 20% by 126-bar return",
             "params": {"setup.min_rs_percentile": 80, "setup.rs_lookback": 126}},
            {"label": "no leadership filter", "params": {"setup.min_rs_percentile": None}},
        ],
    },
    "leading sector": {
        "category": "SECTOR_THEME",
        "description": "A sector outperforming the others.",
        "ambiguity": "Sector strength can be return-based, breadth-based or breakout-count "
                     "based; the ranking engine blends six measures with arbitrary weights.",
        "candidates": [
            {"label": "sector in the top half",
             "params": {"sector_filter.enabled": True,
                        "sector_filter.min_rank_percentile": 50}},
            {"label": "sector in the top quartile",
             "params": {"sector_filter.enabled": True,
                        "sector_filter.min_rank_percentile": 75}},
            {"label": "no sector filter", "params": {"sector_filter.enabled": False}},
        ],
    },
    "strong market": {
        "category": "MARKET_REGIME",
        "description": "Conditions under which breakouts are worth taking.",
        "ambiguity": ("SAR says index 10 MA above 20 MA. The Kullamagi material says "
                      "uptrending OR sideways. These disagree in chop, which is exactly when "
                      "the answer matters."),
        "candidates": [
            {"label": "SPY and QQQ both 10>20 SMA",
             "params": {"market_filter.enabled": True, "market_filter.mode": "both"}},
            {"label": "either SPY or QQQ 10>20 SMA",
             "params": {"market_filter.enabled": True, "market_filter.mode": "either"}},
            {"label": "SPY above its 200 SMA",
             "params": {"market_filter.enabled": True, "market_filter.fast": 1,
                        "market_filter.slow": 200,
                        "market_filter.require_fast_above_slow": False,
                        "market_filter.require_close_above_slow": True,
                        "market_filter.secondary_symbol": None}},
            {"label": "no market filter", "params": {"market_filter.enabled": False}},
        ],
    },
    "trailing stop": {
        "category": "TRADE_MANAGEMENT",
        "description": "How to hold the remainder of a winning position.",
        "ambiguity": "The sources reference 'the 10- and 20-day moving averages' without "
                     "settling which to use.",
        "candidates": [
            {"label": "10 SMA close", "params": {"management.trail.length": 10,
                                                 "management.trail.basis": "close"}},
            {"label": "20 SMA close", "params": {"management.trail.length": 20,
                                                 "management.trail.basis": "close"}},
            {"label": "50 SMA close", "params": {"management.trail.length": 50,
                                                 "management.trail.basis": "close"}},
            {"label": "10 SMA on lows", "params": {"management.trail.length": 10,
                                                   "management.trail.basis": "low"}},
        ],
    },
    "partial profit taking": {
        "category": "TRADE_MANAGEMENT",
        "description": "When and how much to sell into strength.",
        "ambiguity": "SAR: only at +5R. Kullamagi material: 1/3 to 1/2 after 3-5 days. "
                     "These are incompatible policies, not variants of one policy.",
        "candidates": [
            {"label": "25% at +5R (SAR)",
             "params": {"management.partials": [{"at_r": 5.0, "fraction": 0.25,
                                                 "after_days": None}]}},
            {"label": "33% after 3 days (Kullamagi)",
             "params": {"management.partials": [{"at_r": 0.0, "fraction": 0.33,
                                                 "after_days": 3}]}},
            {"label": "50% after 5 days",
             "params": {"management.partials": [{"at_r": 0.0, "fraction": 0.5,
                                                 "after_days": 5}]}},
            {"label": "no partials", "params": {"management.partials": []}},
        ],
    },
}


def candidate_definitions(concept: str) -> list[dict[str, Any]]:
    c = CONCEPTS.get(concept.lower())
    return list(c["candidates"]) if c else []


def formalize(concept: str) -> dict[str, Any]:
    c = CONCEPTS.get(concept.lower())
    if not c:
        return {"found": False, "concept": concept,
                "known_concepts": sorted(CONCEPTS),
                "note": "Unknown concept. Add it to research.formalizer.CONCEPTS with its "
                        "candidate definitions rather than inventing a threshold at runtime."}
    return {"found": True, "concept": concept, **c,
            "how_to_test": ("Each candidate is a set of dotted StrategySpec overrides. Pass "
                            "them to the Hypothesis Lab's definition comparison, which "
                            "backtests every candidate over the same period and universe "
                            "and reports whether the concept survives the choice.")}


def seed_concepts(store: Store = STORE) -> dict[str, Any]:
    n_c = n_d = 0
    for name, c in CONCEPTS.items():
        cid = store.add_concept(name, c["category"], c["description"], c["ambiguity"])
        n_c += 1
        for cand in c["candidates"]:
            store.add_definition(cid, cand["label"], "strategy_override", cand["params"])
            n_d += 1
    return {"concepts": n_c, "definitions": n_d}
