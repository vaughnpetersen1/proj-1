"""Formalised strategies, with provenance.

Each entry records WHERE its rules came from. Nothing in this file is asserted
to work: these are *specifications of claims*, ready for the backtester to
judge. Where two sources disagree (10 SMA vs 20 SMA trailing) both variants are
kept as separate strategies -- see ``research.conflicts`` -- rather than being
averaged into one vague rule.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from .spec import (CostSpec, EntrySpec, ManagementSpec, MarketFilter, PartialExit,
                   RiskSpec, SectorFilter, SetupSpec, StopSpec, StrategySpec, TrailSpec,
                   UniverseSpec)

# ---------------------------------------------------------------------------
# Provenance blocks. Reused by the knowledge base seeder so the KB and the
# strategy library cite exactly the same records.
# ---------------------------------------------------------------------------

SRC_OPERATOR_BRIEF: dict[str, Any] = {
    "source": "Operator strategy brief (SAR / Morgan Trades methodology as supplied)",
    "url": None,
    "author": "Operator",
    "date": "2026-08-29",
    "title": "AI Trading Brain build brief, section 1",
    "source_type": "brief",
    "retrieval_method": "operator_supplied",
    "fulltext_verified": True,
    "note": ("The brief enumerates the strategy's concepts but not their numeric "
             "thresholds. Thresholds below are candidate operationalisations "
             "enumerated by this system, not values stated by the operator."),
}

SRC_SAR_STRATEGY: dict[str, Any] = {
    "source": "SAR Trading / Morgan Trades (first-party site)",
    "url": "https://www.sartrading.io/strategy",
    "author": "Morgan Trades / SAR Trading",
    "date": None,
    "title": "Morgan Trades Swing Trading Strategy",
    "source_type": "article",
    "retrieval_method": "web_search_summary",
    "fulltext_verified": False,
    "note": ("Retrieved via web search result summary. Direct full-text fetch was "
             "blocked by this environment's network egress policy, so the page was "
             "NOT read end-to-end. Re-run `python -m tradingbrain.cli research refresh` "
             "from a machine with egress to verify and upgrade fulltext_verified."),
}

SRC_QULLAMAGGIE_SETUPS: dict[str, Any] = {
    "source": "Qullamaggie (first-party site)",
    "url": "https://qullamaggie.com/my-3-timeless-setups-that-have-made-me-tens-of-millions/",
    "author": "Kristjan Kullamagi",
    "date": None,
    "title": "3 TIMELESS setups that have made me TENS OF MILLIONS",
    "source_type": "article",
    "retrieval_method": "web_search_summary",
    "fulltext_verified": False,
    "note": ("Same retrieval caveat as above: summary only, first-party page not fetched "
             "in full. Treat every number below as a claim attributed to the source, not "
             "as a verified quotation."),
}

SRC_QULLAMAGGIE_EP: dict[str, Any] = {
    "source": "Qullamaggie (first-party site)",
    "url": "https://qullamaggie.com/how-to-master-a-setup-episodic-pivots/",
    "author": "Kristjan Kullamagi",
    "date": None,
    "title": "How to master a setup: Episodic Pivots",
    "source_type": "article",
    "retrieval_method": "web_search_summary",
    "fulltext_verified": False,
    "note": "Summary only; full text not fetched (egress blocked).",
}

SRC_CWT_NOTES: dict[str, Any] = {
    "source": "Chat With Traders interview notes (third-party)",
    "url": "https://tradingresourcehub.substack.com/p/interview-qullamaggie-chat-with-traders-part1",
    "author": "Trading Resource Hub",
    "date": None,
    "title": "Interview Notes: Qullamaggie on Chat With Traders (Part 1)",
    "source_type": "interview_notes",
    "retrieval_method": "web_search_summary",
    "fulltext_verified": False,
    "note": ("THIRD-PARTY interpretation of a first-party interview. Ranked below "
             "first-party sources; used only where it reports figures the "
             "first-party material also discusses."),
}

SRC_KK_STUDY_SITE: dict[str, Any] = {
    "source": "kristjankullamagi.com (independent study guide, third-party)",
    "url": "https://www.kristjankullamagi.com/",
    "author": "Unattributed",
    "date": None,
    "title": "Qullamaggie Setups & Kristjan Kullamagi Story",
    "source_type": "article",
    "retrieval_method": "web_search_summary",
    "fulltext_verified": False,
    "note": "Third-party study guide; used only for discovering primary material.",
}


def _prov(*sources: dict[str, Any], claim: str, interpretation: str) -> list[dict[str, Any]]:
    return [{
        "claim": claim,
        "interpretation": interpretation,
        "evidence_class": "SOURCE_FACT -> INFERENCE (the numeric threshold is this "
                          "system's operationalisation, not the source's words)",
        "sources": [{k: s.get(k) for k in
                     ("source", "url", "author", "date", "title", "source_type",
                      "retrieval_method", "fulltext_verified")} for s in sources],
    } for claim, interpretation in [(claim, interpretation)]]


# ---------------------------------------------------------------------------
# SAR / Morgan Trades v1.0 -- the literal reading of the supplied methodology
# ---------------------------------------------------------------------------

def sar_v1_0() -> StrategySpec:
    return StrategySpec(
        name="SAR Momentum Breakout",
        version="1.0",
        description=(
            "Literal formalisation of the SAR / Morgan Trades five-step breakout as "
            "supplied in the operator brief and described on sartrading.io: a big "
            "multi-week advance, rising 10/20 SMAs, an orderly tightening pullback to "
            "those averages, volume drying up into the base, then a high-volume range "
            "expansion. Index 10 SMA must be above the 20 SMA. Stop at the low of the "
            "breakout day; scale out into strength; trail the remainder with the 10 SMA."
        ),
        universe=UniverseSpec(min_price=5.0, min_dollar_volume=5_000_000.0),
        market_filter=MarketFilter(enabled=True, symbol="SPY", secondary_symbol="QQQ",
                                   fast=10, slow=20, mode="both"),
        sector_filter=SectorFilter(enabled=False),
        setup=SetupSpec(
            prior_move_min_pct=30.0, prior_move_lookback=63, prior_move_max_bars=60,
            prior_move_min_bars=3,
            cons_min_len=5, cons_max_len=40, cons_max_depth_pct=35.0,
            cons_max_range_ratio=1.00, cons_max_volume_ratio=1.00,
            cons_require_ma_support=True, cons_ma_len=20,
            cons_min_close_above_ma_frac=0.60,
            require_close_above_smas=[10, 20],
            require_sma_stack=[],
            max_pct_above_sma20=None,
            min_r2_20=None,
        ),
        entry=EntrySpec(type="daily_breakout", breakout_min_rvol=1.5,
                        breakout_require_gt_prev_volume=True,
                        breakout_min_close_position=0.50, fill="next_open"),
        stop=StopSpec(type="low_of_day", max_stop_adr_multiple=None, max_stop_pct=None),
        management=ManagementSpec(
            partials=[PartialExit(at_r=5.0, fraction=0.25, after_days=None)],
            breakeven_after_r=None,
            trail=TrailSpec(type="sma", length=10, basis="close"),
            max_hold_days=180),
        risk=RiskSpec(risk_pct=1.0, max_positions=8, max_position_pct=20.0),
        costs=CostSpec(),
        provenance=(
            _prov(SRC_SAR_STRATEGY, SRC_OPERATOR_BRIEF,
                  claim="A big move up of 30%+ over multiple days to weeks, not a one-day pump.",
                  interpretation="prior_move_min_pct=30, min_bars=3, searched over 63 bars.")
            + _prov(SRC_SAR_STRATEGY,
                    claim="10/20 SMA inclining; orderly pullback to the 10/20 SMA with a "
                          "tightening range; volume drying up as the stock pulls back.",
                    interpretation="cons_max_range_ratio<=1.0 and cons_max_volume_ratio<=1.0 "
                                   "measured as 2nd-half vs 1st-half of the base; base must "
                                   "hold above the 20 SMA on >=60% of closes.")
            + _prov(SRC_SAR_STRATEGY,
                    claim="A breakout of that range on high volume.",
                    interpretation="close above the base high with relative volume >= 1.5x "
                                   "the 50-day average and volume above the prior day.")
            + _prov(SRC_SAR_STRATEGY,
                    claim="You only sell when you are up 5x your risk or more, and the first "
                          "sell is only a portion (10-30%) of the position.",
                    interpretation="partial exit of 25% at +5R.")
            + _prov(SRC_OPERATOR_BRIEF,
                    claim="Avoid breakouts when SPY/QQQ 10 MA is below the 20 MA.",
                    interpretation="market_filter requires 10 SMA > 20 SMA on BOTH SPY and "
                                   "QQQ. Explicitly flagged in the brief as a rule to test, "
                                   "so a filter-off control arm exists as a separate strategy.")
        ),
        notes=("Numeric thresholds are this system's operationalisation of qualitative "
               "language. Competing operationalisations live in research.formalizer and "
               "are what the Hypothesis Lab actually compares."),
    )


def sar_v1_1_adr_stop() -> StrategySpec:
    """v1.0 plus Kullamagi's ADR stop cap and his scale-out timing."""
    s = sar_v1_0()
    return s.bumped(
        "1.1",
        name="SAR Momentum Breakout",
        description=(s.description + " v1.1 adds Kullamagi's public risk constraint: skip "
                     "the trade when the low-of-day stop is wider than 1x ADR(20), and take "
                     "a first partial 3-5 days after entry rather than waiting for +5R."),
        stop={**dataclasses.asdict(s.stop), "max_stop_adr_multiple": 1.0},
        management={
            "partials": [{"at_r": 1.0, "fraction": 0.33, "after_days": 3}],
            "breakeven_after_r": 1.0, "breakeven_after_days": None,
            "trail": {"type": "sma", "length": 10, "multiple": 2.0,
                      "basis": "close", "activate_after_r": None},
            "max_hold_days": 180, "time_stop_days": None, "time_stop_min_r": 0.0,
            "target_r": None,
        },
        provenance=s.provenance + _prov(
            SRC_QULLAMAGGIE_SETUPS,
            claim="The initial stop is the low of the day, and the stop should never be "
                  "wider than 1x the stock's ADR; if the day's low is more than the ADR "
                  "below entry, the trade is skipped.",
            interpretation="stop.max_stop_adr_multiple = 1.0, evaluated against ADR(20).")
        + _prov(SRC_CWT_NOTES, SRC_QULLAMAGGIE_SETUPS,
                claim="Sell one-third to half of the position 3-5 days after entry and move "
                      "the stop to breakeven.",
                interpretation="partial of 33% at the earlier of +1R or 3 days held, then "
                               "stop to breakeven."),
    )


def kullamagi_breakout_v1_0() -> StrategySpec:
    """Continuation breakout as publicly described by Kullamagi."""
    return StrategySpec(
        name="Kullamagi Continuation Breakout",
        version="1.0",
        description=(
            "Public-source formalisation of the continuation/breakout setup: a 30-100%+ "
            "advance over the prior 1-3 months, then a 1-3 week orderly consolidation with "
            "higher lows and a tightening range riding the rising 10/20 (sometimes 50) SMA, "
            "entered on a range expansion. Stop at the low of the day, capped at 1x ADR. "
            "Scale out into strength after 3-5 days, then trail the 10 or 20 SMA."),
        universe=UniverseSpec(min_price=5.0, min_dollar_volume=5_000_000.0, min_adr_pct=2.0),
        market_filter=MarketFilter(enabled=True, symbol="SPY", secondary_symbol="QQQ",
                                   fast=10, slow=20, mode="either"),
        setup=SetupSpec(
            prior_move_min_pct=30.0, prior_move_lookback=63, prior_move_max_bars=63,
            prior_move_min_bars=5,
            cons_min_len=5, cons_max_len=20, cons_max_depth_pct=25.0,
            cons_max_range_ratio=1.00, cons_max_volume_ratio=1.05,
            cons_require_ma_support=True, cons_ma_len=20,
            cons_min_close_above_ma_frac=0.70,
            require_close_above_smas=[10, 20],
            require_sma_stack=[10, 20, 50],
        ),
        entry=EntrySpec(type="daily_breakout", breakout_min_rvol=1.3,
                        breakout_min_close_position=0.50, fill="next_open"),
        stop=StopSpec(type="low_of_day", max_stop_adr_multiple=1.0),
        management=ManagementSpec(
            partials=[PartialExit(at_r=0.0, fraction=0.33, after_days=4)],
            breakeven_after_r=None, breakeven_after_days=4,
            trail=TrailSpec(type="sma", length=10, basis="close"),
            max_hold_days=180),
        risk=RiskSpec(risk_pct=0.75, max_positions=6, max_position_pct=20.0),
        provenance=(
            _prov(SRC_QULLAMAGGIE_SETUPS, SRC_KK_STUDY_SITE,
                  claim="A big move higher in the past 1-3 months (30-100%+), an orderly "
                        "consolidation with higher lows and a tightening range over 1-3 "
                        "weeks, then a range expansion out of that consolidation.",
                  interpretation="prior move >=30% inside 63 bars; base length 5-20 bars; "
                                 "range contracting; higher-low structure scored, not required.")
            + _prov(SRC_QULLAMAGGIE_SETUPS,
                    claim="During the consolidation price surfs the rising 10- and 20-day "
                          "and sometimes the 50-day moving average.",
                    interpretation="require_sma_stack=[10,20,50] and >=70% of base closes "
                                   "above the 20 SMA.")
            + _prov(SRC_QULLAMAGGIE_SETUPS,
                    claim="Entry on the opening range high (first 1/5/60-minute candle).",
                    interpretation="NOT modelled in this daily-bar strategy. The "
                                   "opening_range entry type exists and requires intraday "
                                   "bars; with daily-only data this strategy uses the "
                                   "conservative next-open fill. The difference between the "
                                   "two is itself an open hypothesis (see Research Lab).")
            + _prov(SRC_CWT_NOTES,
                    claim="Positions are typically 10-20% of account with risk of 0.25-1% "
                          "per trade, rarely more than 1%.",
                    interpretation="risk_pct=0.75, max_position_pct=20.")
        ),
    )


def sar_no_market_filter_v1_0() -> StrategySpec:
    """Control arm: identical to SAR v1.0 with the index filter switched off."""
    s = sar_v1_0()
    out = s.bumped("1.0-nofilter", name="SAR Momentum Breakout (no market filter)",
                   description=("Control arm for the market-regime experiment. Identical to "
                                "SAR v1.0 except the SPY/QQQ 10>20 SMA filter is disabled, so "
                                "the filter's contribution can be measured rather than assumed."))
    out.market_filter.enabled = False
    return out


def sar_trail20_v1_0() -> StrategySpec:
    """Knowledge-conflict arm: trail the 20 SMA instead of the 10 SMA."""
    s = sar_v1_1_adr_stop()
    out = s.bumped("1.1-trail20", name="SAR Momentum Breakout (20 SMA trail)",
                   description=("Conflict arm. The sources describe trailing with 'the 10- and "
                                "20-day moving averages' without settling which. This arm "
                                "trails the 20 SMA so the two can be compared directly."))
    out.management.trail = TrailSpec(type="sma", length=20, basis="close")
    return out


def episodic_pivot_v0_1() -> StrategySpec:
    """Catalyst gap continuation. Marked v0.1 because the catalyst leg is unmodelled."""
    return StrategySpec(
        name="Episodic Pivot (partial)",
        version="0.1",
        description=(
            "INCOMPLETE BY DESIGN. The episodic pivot is defined by a *catalyst* -- an "
            "earnings surprise or news event that forces a repricing. Without a "
            "point-in-time news/earnings feed this system cannot identify the catalyst, "
            "so this specification captures only the price/volume signature: a large gap "
            "up on enormous relative volume from a quiet base, out of a name that has not "
            "already run. Any statistics produced from it describe 'large gap ups', not "
            "episodic pivots, and must be read that way."),
        universe=UniverseSpec(min_price=3.0, min_dollar_volume=1_000_000.0),
        market_filter=MarketFilter(enabled=False),
        setup=SetupSpec(
            prior_move_min_pct=0.0, prior_move_lookback=21, prior_move_max_bars=21,
            prior_move_min_bars=1,
            cons_min_len=10, cons_max_len=60, cons_max_depth_pct=45.0,
            cons_max_range_ratio=1.30, cons_max_volume_ratio=1.30,
            cons_require_ma_support=False,
            require_close_above_smas=[],
            max_pct_above_sma20=None),
        entry=EntrySpec(type="daily_breakout", breakout_min_rvol=4.0,
                        breakout_require_gt_prev_volume=True,
                        breakout_min_close_position=0.50,
                        breakout_buffer_pct=5.0, fill="next_open"),
        stop=StopSpec(type="low_of_day", max_stop_adr_multiple=None),
        management=ManagementSpec(
            partials=[PartialExit(at_r=1.0, fraction=0.33, after_days=3)],
            breakeven_after_r=1.0,
            trail=TrailSpec(type="sma", length=20, basis="close"),
            max_hold_days=90),
        risk=RiskSpec(risk_pct=0.5, max_positions=5),
        provenance=_prov(
            SRC_QULLAMAGGIE_EP,
            claim="Episodic pivots are triggered by a major catalyst that suddenly changes "
                  "how the market values a company, forcing a rapid repricing; the concept "
                  "traces to Pradeep Bonde / Stockbee's work on gap-ups from earnings "
                  "surprises.",
            interpretation="UNRESOLVED. Catalyst detection requires a point-in-time news and "
                           "earnings feed, which is not available. The price signature alone "
                           "(gap >5% on >4x relative volume out of a quiet base) is a strict "
                           "superset of the real setup."),
        notes="Do not promote past v0.x until a point-in-time catalyst source exists.",
    )


LIBRARY: dict[str, Any] = {
    "sar_v1_0": sar_v1_0,
    "sar_v1_1_adr_stop": sar_v1_1_adr_stop,
    "sar_no_market_filter_v1_0": sar_no_market_filter_v1_0,
    "sar_trail20_v1_0": sar_trail20_v1_0,
    "kullamagi_breakout_v1_0": kullamagi_breakout_v1_0,
    "episodic_pivot_v0_1": episodic_pivot_v0_1,
}


def get_strategy(key: str) -> StrategySpec:
    if key not in LIBRARY:
        raise KeyError(f"unknown strategy {key!r}; known: {sorted(LIBRARY)}")
    return LIBRARY[key]()


def list_strategies() -> list[dict[str, Any]]:
    out = []
    for key, fn in LIBRARY.items():
        s = fn()
        out.append({"key": key, "name": s.name, "version": s.version,
                    "description": s.description, "fingerprint": s.fingerprint(),
                    "provenance_count": len(s.provenance)})
    return out
