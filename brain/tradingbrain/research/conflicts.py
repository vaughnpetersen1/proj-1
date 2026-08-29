"""Knowledge conflict engine.

When sources disagree, the disagreement is the finding. Merging "use the 10 SMA"
and "use the 20 SMA" into "use a short moving average" destroys the only thing
that could have been tested. So conflicts are stored intact, each side is turned
into a runnable strategy variant, and the experiment decides -- for this
operator, on this data.
"""

from __future__ import annotations

from typing import Any

from ..db.store import STORE, Store
from .hypothesis import HypothesisSpec

KNOWN_CONFLICTS: list[dict[str, Any]] = [
    {
        "id": "trail_10_vs_20",
        "topic": "Which moving average trails the remainder of a winning position?",
        "side_a": {"source": "Kullamagi material (interview notes)",
                   "position": "Trail the 10-day moving average; exit on a close below it.",
                   "strategy_key": "sar_v1_1_adr_stop"},
        "side_b": {"source": "Same material, alternative reading",
                   "position": "Trail the 20-day moving average.",
                   "strategy_key": "sar_trail20_v1_0"},
        "why_unresolved": ("The sources reference 'the 10- and 20-day moving averages' as a "
                           "pair without stating which trails the position. Both readings are "
                           "defensible from the text."),
        "experiment": HypothesisSpec(kind="variants", concept="trailing stop",
                                     question="Does trailing with the 10 SMA produce better "
                                              "risk-adjusted returns than the 20 SMA?").to_dict(),
    },
    {
        "id": "partials_5r_vs_3days",
        "topic": "When is the first partial taken?",
        "side_a": {"source": "SAR Trading / Morgan Trades",
                   "position": "Only sell when up 5x risk or more, then 10-30% of the position.",
                   "strategy_key": "sar_v1_0"},
        "side_b": {"source": "Kullamagi material",
                   "position": "Sell one-third to one-half 3-5 days after entry regardless "
                               "of R, then move the stop to breakeven.",
                   "strategy_key": "sar_v1_1_adr_stop"},
        "why_unresolved": ("These are incompatible policies, not variants of one policy. One "
                           "is R-triggered and rare; the other is time-triggered and "
                           "unconditional. Averaging them would produce a rule neither source "
                           "endorses."),
        "experiment": HypothesisSpec(kind="variants", concept="partial profit taking",
                                     question="Does taking a time-based partial beat waiting "
                                              "for +5R?").to_dict(),
    },
    {
        "id": "market_filter_strictness",
        "topic": "What market conditions permit taking breakouts?",
        "side_a": {"source": "SAR / operator brief",
                   "position": "Avoid breakouts when the index 10 MA is below the 20 MA.",
                   "strategy_key": "sar_v1_0"},
        "side_b": {"source": "Kullamagi interview notes",
                   "position": "Trade breakouts in uptrending OR sideways markets; only avoid "
                               "downtrends.",
                   "strategy_key": "sar_no_market_filter_v1_0"},
        "why_unresolved": ("The two rules agree in clear uptrends and clear downtrends and "
                           "disagree in chop -- which is where most of the year is spent, so "
                           "the disagreement is material rather than academic."),
        "experiment": HypothesisSpec(kind="filter", filter_path="market_filter.enabled",
                                     question="Does the index 10>20 SMA filter improve "
                                              "expectancy and drawdown?").to_dict(),
    },
    {
        "id": "stop_width",
        "topic": "How wide may the initial stop be?",
        "side_a": {"source": "SAR Trading",
                   "position": "Stops are typically 2-5% from entry.",
                   "strategy_key": "sar_v1_0"},
        "side_b": {"source": "Kullamagi material",
                   "position": "Stop at the low of the day, never wider than 1x ADR; skip "
                               "the trade otherwise.",
                   "strategy_key": "sar_v1_1_adr_stop"},
        "why_unresolved": ("A fixed percentage and an ADR multiple pick different trades: for "
                           "a 6% ADR name the ADR rule permits a stop the percentage rule "
                           "forbids, and vice versa for a 1.5% ADR name."),
        "experiment": HypothesisSpec(kind="variants", concept="extended",
                                     question="Does the ADR stop cap improve risk-adjusted "
                                              "returns versus a fixed percentage cap?",
                                     overrides=[
                                         {"label": "ADR cap 1.0x",
                                          "params": {"stop.max_stop_adr_multiple": 1.0,
                                                     "stop.max_stop_pct": None}},
                                         {"label": "fixed 5% cap",
                                          "params": {"stop.max_stop_adr_multiple": None,
                                                     "stop.max_stop_pct": 5.0}},
                                         {"label": "no cap",
                                          "params": {"stop.max_stop_adr_multiple": None,
                                                     "stop.max_stop_pct": None}},
                                     ]).to_dict(),
    },
]


def conflict_report(store: Store = STORE) -> dict[str, Any]:
    out = []
    for c in KNOWN_CONFLICTS:
        exps = store.query(
            "SELECT id, name, verdict, conclusion, sample_size, created_at, data_origin "
            "FROM experiments WHERE name LIKE ? ORDER BY id DESC LIMIT 3",
            (f"%{c['experiment'].get('concept') or c['experiment'].get('filter_path')}%",))
        out.append({**c, "evidence": exps,
                    "status": "RESOLVED_BY_EVIDENCE" if exps else "OPEN",
                    "note": ("No experiment has adjudicated this conflict yet. Both positions "
                             "remain recorded; neither is treated as the system's belief."
                             if not exps else
                             "See the linked experiment(s). Evidence adjudicates for THIS "
                             "operator on THIS data, not universally.")})
    return {"conflicts": out, "open": sum(1 for c in out if c["status"] == "OPEN"),
            "principle": ("Disagreeing sources are preserved, never merged. Each side becomes "
                          "a runnable strategy variant and the backtest decides.")}
