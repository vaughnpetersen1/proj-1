"""Seed source records and extracted knowledge items.

PROVENANCE HONESTY NOTE (read this before trusting anything below)
------------------------------------------------------------------
This build environment's egress proxy denies HTTPS CONNECT to every non-package
host, so first-party pages could NOT be fetched in full. What was available was
web *search*, which returns result titles, URLs and a synthesised summary.

Consequently every record whose ``retrieval_method`` is ``web_search_summary``
has ``fulltext_verified = False`` and carries **no verbatim quote**. The
``observation`` field is a paraphrase of what search results attribute to the
source -- it is recorded as "the source is reported to say X", never as
"X is true", and never as a quotation.

Run ``python -m tradingbrain.cli research refresh`` from a machine with network
egress to fetch the first-party pages, attach real excerpts, and flip
``fulltext_verified`` to true. Until then the Knowledge Base UI shows an
UNVERIFIED badge on these rows.

The one fully verified source is the operator's own strategy brief, which was
supplied directly.
"""

from __future__ import annotations

from typing import Any

from ...strategy.library import (SRC_CWT_NOTES, SRC_KK_STUDY_SITE, SRC_OPERATOR_BRIEF,
                                 SRC_QULLAMAGGIE_EP, SRC_QULLAMAGGIE_SETUPS,
                                 SRC_SAR_STRATEGY)

#: Additional first-party / primary URLs discovered while researching, recorded so
#: the refresh pipeline knows where to look. Nothing has been extracted from them.
DISCOVERED_SOURCES: list[dict[str, Any]] = [
    {"source": "Qullamaggie (first-party site)", "url": "https://qullamaggie.com/",
     "author": "Kristjan Kullamagi", "title": "Qullamaggie -- home",
     "source_type": "website", "topic": "index of first-party material",
     "retrieval_method": "search_result_link", "fulltext_verified": False,
     "note": "Discovered, not fetched. Priority 1 for the refresh pipeline."},
    {"source": "SAR Trading (first-party site)", "url": "https://www.sartrading.io/",
     "author": "Morgan Trades / SAR Trading", "title": "SAR Trading -- home",
     "source_type": "website", "topic": "index of first-party material",
     "retrieval_method": "search_result_link", "fulltext_verified": False,
     "note": "Discovered, not fetched. Priority 1 for the refresh pipeline."},
    {"source": "Chat With Traders (podcast, first-party interview)",
     "url": "https://tradingresourcehub.substack.com/p/qullamaggie-chat-with-traders-reimagined-transcript",
     "author": "Chat With Traders / Trading Resource Hub",
     "title": "Qullamaggie on Chat With Traders (transcript, third-party hosted)",
     "source_type": "interview_transcript", "topic": "process, risk, psychology",
     "retrieval_method": "search_result_link", "fulltext_verified": False,
     "note": "Third-party hosted transcript of a first-party interview. Prefer the "
             "original podcast audio/transcript if it can be located."},
]

# ---------------------------------------------------------------------------
# category | concept | observation (paraphrase) | rule implied | explicit?
#  + candidate quantitative interpretations + backtest hypothesis
# ---------------------------------------------------------------------------

KNOWLEDGE: list[dict[str, Any]] = [
    # ---------------- SAR / Morgan Trades -----------------------------------
    {
        "src": SRC_SAR_STRATEGY, "category": "SETUP", "concept": "large prior expansion",
        "observation": ("Search results attribute to sartrading.io a five-step breakout "
                        "process beginning with a big move up of 30%+ spanning multiple days "
                        "to weeks -- explicitly not a single-day pump."),
        "rule_text": "Require a multi-day advance of at least ~30% before considering a base.",
        "explicit": True,
        "quant": [
            {"label": "+30% within 60 bars, searched over 63", "detector": "prior_move",
             "params": {"min_pct": 30, "max_bars": 60, "lookback": 63, "min_bars": 3}},
            {"label": "+20% within 10 bars", "detector": "prior_move",
             "params": {"min_pct": 20, "max_bars": 10, "lookback": 63, "min_bars": 3}},
            {"label": "+50% within 30 bars", "detector": "prior_move",
             "params": {"min_pct": 50, "max_bars": 30, "lookback": 63, "min_bars": 3}},
            {"label": "+100% within 63 bars", "detector": "prior_move",
             "params": {"min_pct": 100, "max_bars": 63, "lookback": 126, "min_bars": 5}},
        ],
        "hypothesis": ("Breakouts preceded by a larger prior advance have higher forward "
                       "expectancy than breakouts without one, and the relationship is "
                       "monotone in the size of the advance."),
        "component": "SetupSpec.prior_move_min_pct",
    },
    {
        "src": SRC_SAR_STRATEGY, "category": "SETUP", "concept": "orderly pullback / tight range",
        "observation": ("Reported steps 2-3: the 10 and 20 SMA are inclining and price makes "
                        "an orderly pullback to them with higher lows and lower highs -- a "
                        "tightening range."),
        "rule_text": "Base must tighten (contracting range) and hold the rising 10/20 SMA.",
        "explicit": True,
        "quant": [
            {"label": "2nd-half daily range < 1st-half (ratio <= 1.0)",
             "detector": "consolidation", "params": {"max_range_ratio": 1.0}},
            {"label": "strict tightening (ratio <= 0.8)", "detector": "consolidation",
             "params": {"max_range_ratio": 0.8}},
            {"label": "loose (ratio <= 1.2)", "detector": "consolidation",
             "params": {"max_range_ratio": 1.2}},
            {"label": "depth cap 15% instead of range ratio", "detector": "consolidation",
             "params": {"max_depth_pct": 15.0, "max_range_ratio": 99.0}},
        ],
        "hypothesis": ("Bases whose second half is tighter than the first produce higher "
                       "expectancy on the subsequent breakout than bases that do not tighten."),
        "component": "SetupSpec.cons_max_range_ratio",
    },
    {
        "src": SRC_SAR_STRATEGY, "category": "SETUP", "concept": "volume dry-up",
        "observation": ("Reported step 4: volume dries up as the stock pulls back into the "
                        "base."),
        "rule_text": "Volume in the second half of the base should be below the first half.",
        "explicit": True,
        "quant": [
            {"label": "2nd-half volume <= 1st-half", "detector": "consolidation",
             "params": {"max_volume_ratio": 1.0}},
            {"label": "volume halves (<= 0.7)", "detector": "consolidation",
             "params": {"max_volume_ratio": 0.7}},
            {"label": "no volume condition", "detector": "consolidation",
             "params": {"max_volume_ratio": 99.0}},
        ],
        "hypothesis": ("Volume contraction during the base improves breakout expectancy. "
                       "This is the operator's own worked example in the brief and is the "
                       "seeded hypothesis in the Hypothesis Lab."),
        "component": "SetupSpec.cons_max_volume_ratio",
    },
    {
        "src": SRC_SAR_STRATEGY, "category": "ENTRY", "concept": "high-volume breakout",
        "observation": "Reported step 5: a breakout of the range on high volume.",
        "rule_text": "Enter on a range expansion above the base high, confirmed by volume.",
        "explicit": True,
        "quant": [
            {"label": "relative volume >= 1.5x the 50-day average", "detector": "breakout",
             "params": {"min_rvol": 1.5}},
            {"label": "relative volume >= 1.0x (no volume filter in practice)",
             "detector": "breakout", "params": {"min_rvol": 1.0}},
            {"label": "relative volume >= 2.5x", "detector": "breakout",
             "params": {"min_rvol": 2.5}},
            {"label": "close in the upper 75% of the bar", "detector": "breakout",
             "params": {"min_close_position": 0.75}},
        ],
        "hypothesis": "Higher breakout-day relative volume raises forward expectancy.",
        "component": "EntrySpec.breakout_min_rvol",
    },
    {
        "src": SRC_SAR_STRATEGY, "category": "RISK", "concept": "stop width",
        "observation": ("Search results report SAR stops as typically 2-5% wide from the "
                        "entry price."),
        "rule_text": "Keep the initial stop within roughly 2-5% of entry.",
        "explicit": True,
        "quant": [
            {"label": "hard 5% cap on stop width", "detector": "stop",
             "params": {"max_stop_pct": 5.0}},
            {"label": "hard 3% cap", "detector": "stop", "params": {"max_stop_pct": 3.0}},
            {"label": "no cap (low of day wherever it lands)", "detector": "stop",
             "params": {"max_stop_pct": None}},
        ],
        "hypothesis": ("Capping stop width filters out setups whose signal-bar range is too "
                       "wide, and improves expectancy per unit of risk -- or it removes the "
                       "highest-momentum names and hurts it. Untested."),
        "component": "StopSpec.max_stop_pct",
    },
    {
        "src": SRC_SAR_STRATEGY, "category": "TRADE_MANAGEMENT", "concept": "partial profit taking",
        "observation": ("Search results attribute to SAR: sell only when up 5x risk or more, "
                        "and the first sell is a portion (10-30%) of the position, taken a "
                        "few minutes before the close."),
        "rule_text": "Do not take partials before +5R; then scale 10-30%.",
        "explicit": True,
        "quant": [
            {"label": "25% at +5R", "detector": "management",
             "params": {"partials": [{"at_r": 5.0, "fraction": 0.25}]}},
            {"label": "33% at +1R", "detector": "management",
             "params": {"partials": [{"at_r": 1.0, "fraction": 0.33}]}},
            {"label": "33% after 3 days", "detector": "management",
             "params": {"partials": [{"at_r": 0.0, "fraction": 0.33, "after_days": 3}]}},
            {"label": "no partials", "detector": "management", "params": {"partials": []}},
        ],
        "hypothesis": ("DIRECT CONFLICT with the Kullamagi material, which describes selling "
                       "into strength 3-5 days after entry regardless of R. Both are "
                       "preserved and compared; see the Knowledge Conflicts view."),
        "component": "ManagementSpec.partials",
    },
    {
        "src": SRC_SAR_STRATEGY, "category": "SETUP", "concept": "timeframe",
        "observation": ("Reported: 99% of setups are found on the daily chart, with some on "
                        "the weekly; positions are held days to weeks and sometimes months."),
        "rule_text": "Source setups from daily bars; hold across days to weeks.",
        "explicit": True,
        "quant": [{"label": "daily bars only", "detector": "timeframe",
                   "params": {"timeframe": "1d"}}],
        "hypothesis": "N/A -- a scoping statement rather than a testable edge claim.",
        "component": "data.Timeframe",
    },
    {
        "src": SRC_OPERATOR_BRIEF, "category": "MARKET_REGIME", "concept": "index MA filter",
        "observation": ("The operator's brief states the SAR source discusses avoiding "
                        "breakouts when the SPY/QQQ 10 MA is below the 20 MA, and explicitly "
                        "instructs that this be treated as a testable rule rather than obeyed."),
        "rule_text": "Skip breakout entries while the index 10 SMA is below the 20 SMA.",
        "explicit": True,
        "quant": [
            {"label": "both SPY and QQQ 10>20", "detector": "market_filter",
             "params": {"mode": "both", "fast": 10, "slow": 20}},
            {"label": "either SPY or QQQ 10>20", "detector": "market_filter",
             "params": {"mode": "either", "fast": 10, "slow": 20}},
            {"label": "SPY above its 200 SMA", "detector": "market_filter",
             "params": {"fast": 1, "slow": 200, "require_close_above_slow": True}},
            {"label": "no market filter", "detector": "market_filter",
             "params": {"enabled": False}},
        ],
        "hypothesis": ("Applying the index 10>20 SMA filter improves expectancy, profit "
                       "factor and drawdown versus taking every qualifying breakout."),
        "component": "MarketFilter",
    },
    {
        "src": SRC_OPERATOR_BRIEF, "category": "STOCK_SELECTION",
        "concept": "leading stocks / relative strength",
        "observation": ("The brief lists sector strength, theme strength, leading stocks and "
                        "relative strength as core selection concepts, without numeric "
                        "definitions."),
        "rule_text": "Prefer stocks leading their group and the broad market.",
        "explicit": False,
        "quant": [
            {"label": "top 20% of universe by 63-bar return", "detector": "relative_strength",
             "params": {"min_rs_percentile": 80, "rs_lookback": 63}},
            {"label": "top 50%", "detector": "relative_strength",
             "params": {"min_rs_percentile": 50, "rs_lookback": 63}},
            {"label": "top 20% by 126-bar return", "detector": "relative_strength",
             "params": {"min_rs_percentile": 80, "rs_lookback": 126}},
            {"label": "no relative-strength filter", "detector": "relative_strength",
             "params": {"min_rs_percentile": None}},
        ],
        "hypothesis": ("Requiring a high relative-strength percentile at the breakout "
                       "improves out-of-sample expectancy."),
        "component": "SetupSpec.min_rs_percentile",
    },
    {
        "src": SRC_OPERATOR_BRIEF, "category": "SETUP", "concept": "orderly / linear movement",
        "observation": ("The brief names 'orderly/linear price movement' as a quality "
                        "criterion but supplies no measurement."),
        "rule_text": "Prefer stocks whose advance is smooth rather than erratic.",
        "explicit": False,
        "quant": [
            {"label": "R^2 of a linear fit to 20 log-closes >= 0.7",
             "detector": "orderliness", "params": {"min_r2_20": 0.7}},
            {"label": "R^2 >= 0.5", "detector": "orderliness", "params": {"min_r2_20": 0.5}},
            {"label": "no orderliness filter", "detector": "orderliness",
             "params": {"min_r2_20": None}},
        ],
        "hypothesis": ("'Orderly' advances (high linear-fit R^2) produce better breakout "
                       "outcomes than erratic ones of the same magnitude."),
        "component": "SetupSpec.min_r2_20",
    },
    {
        "src": SRC_OPERATOR_BRIEF, "category": "ENTRY", "concept": "intraday confirmation",
        "observation": ("The brief lists opening-range, 1-minute and 5-minute confirmation as "
                        "optional entry refinements."),
        "rule_text": "Optionally require an intraday trigger before entering.",
        "explicit": True,
        "quant": [
            {"label": "5-minute opening range high", "detector": "entry",
             "params": {"type": "opening_range", "opening_range_minutes": 5}},
            {"label": "1-minute opening range high", "detector": "entry",
             "params": {"type": "opening_range", "opening_range_minutes": 1}},
            {"label": "60-minute opening range high", "detector": "entry",
             "params": {"type": "opening_range", "opening_range_minutes": 60}},
            {"label": "next-day open (no intraday trigger)", "detector": "entry",
             "params": {"type": "daily_breakout", "fill": "next_open"}},
        ],
        "hypothesis": ("Opening-range confirmation improves expectancy versus a next-open "
                       "fill. UNTESTABLE in this build: no intraday data source is reachable, "
                       "so the comparison would run on generated intraday bars and prove "
                       "nothing. Recorded as blocked, not as unsupported."),
        "component": "EntrySpec.type",
    },

    # ---------------- Kullamagi ---------------------------------------------
    {
        "src": SRC_QULLAMAGGIE_SETUPS, "category": "SETUP", "concept": "continuation breakout",
        "observation": ("Search results attribute to Kullamagi's material three structural "
                        "conditions: a big move in the past 1-3 months (30-100%+), an orderly "
                        "consolidation with higher lows and a tightening range over roughly "
                        "1-3 weeks, then a range expansion out of that consolidation."),
        "rule_text": "Buy the range expansion out of a 1-3 week base following a 30-100% run.",
        "explicit": True,
        "quant": [
            {"label": "base 5-20 bars (1-4 weeks)", "detector": "consolidation",
             "params": {"min_len": 5, "max_len": 20}},
            {"label": "base 10-40 bars (2 weeks-2 months)", "detector": "consolidation",
             "params": {"min_len": 10, "max_len": 40}},
            {"label": "base 3-10 bars (tight, short)", "detector": "consolidation",
             "params": {"min_len": 3, "max_len": 10}},
        ],
        "hypothesis": "Base length has an optimum; too short is noise, too long is a topping "
                      "pattern. Test expectancy as a function of base length.",
        "component": "SetupSpec.cons_min_len / cons_max_len",
    },
    {
        "src": SRC_QULLAMAGGIE_SETUPS, "category": "SETUP", "concept": "moving-average surfing",
        "observation": ("Reported: during the consolidation the stock 'surfs' the rising 10- "
                        "and 20-day, and sometimes the 50-day, moving average."),
        "rule_text": "Base should hold above the rising 10/20 SMA; 50 SMA for longer bases.",
        "explicit": True,
        "quant": [
            {"label": ">=60% of base closes above the 20 SMA", "detector": "consolidation",
             "params": {"cons_ma_len": 20, "min_close_above_ma_frac": 0.6}},
            {"label": ">=80% above the 10 SMA", "detector": "consolidation",
             "params": {"cons_ma_len": 10, "min_close_above_ma_frac": 0.8}},
            {"label": "10>20>50 SMA stack required at the breakout",
             "detector": "trend", "params": {"require_sma_stack": [10, 20, 50]}},
        ],
        "hypothesis": "Requiring the 10>20>50 stack at the breakout improves expectancy.",
        "component": "SetupSpec.require_sma_stack",
    },
    {
        "src": SRC_QULLAMAGGIE_SETUPS, "category": "ENTRY", "concept": "opening range entry",
        "observation": ("Reported: entry is taken on the opening range high -- the high of "
                        "the first 1-minute, 5-minute or 60-minute candle."),
        "rule_text": "Trigger on a break of the opening range high.",
        "explicit": True,
        "quant": [{"label": "first 5-minute candle high", "detector": "entry",
                   "params": {"type": "opening_range", "opening_range_minutes": 5}}],
        "hypothesis": "See 'intraday confirmation' -- blocked by intraday data availability.",
        "component": "EntrySpec.type",
    },
    {
        "src": SRC_QULLAMAGGIE_SETUPS, "category": "RISK", "concept": "ADR-capped stop",
        "observation": ("Reported: the initial stop is the low of the day, and it should "
                        "never be wider than 1x the stock's ADR; if the day's low is more "
                        "than one ADR below entry, the trade is skipped."),
        "rule_text": "Stop = low of day, capped at 1x ADR(20); otherwise skip the trade.",
        "explicit": True,
        "quant": [
            {"label": "skip if stop > 1.0x ADR(20)", "detector": "stop",
             "params": {"max_stop_adr_multiple": 1.0}},
            {"label": "skip if stop > 1.5x ADR(20)", "detector": "stop",
             "params": {"max_stop_adr_multiple": 1.5}},
            {"label": "no ADR cap", "detector": "stop",
             "params": {"max_stop_adr_multiple": None}},
        ],
        "hypothesis": ("The ADR stop cap improves risk-adjusted returns by removing setups "
                       "whose signal bar was too wide -- or it removes the best movers. "
                       "Directly testable against the same strategy without the cap."),
        "component": "StopSpec.max_stop_adr_multiple",
    },
    {
        "src": SRC_CWT_NOTES, "category": "RISK", "concept": "risk per trade and exposure",
        "observation": ("Interview notes report: most positions 10-20% of account, risk on "
                        "most trades 0.25-1%, rarely more than 1%, and no more than ~30% of "
                        "the account in a single stock or ETF overnight."),
        "rule_text": "Risk 0.25-1% per trade; position 10-20%; single-name overnight cap ~30%.",
        "explicit": True,
        "quant": [
            {"label": "risk 1.0% per trade", "detector": "risk", "params": {"risk_pct": 1.0}},
            {"label": "risk 0.5% per trade", "detector": "risk", "params": {"risk_pct": 0.5}},
            {"label": "risk 0.25% per trade", "detector": "risk", "params": {"risk_pct": 0.25}},
        ],
        "hypothesis": ("Risk per trade does not change expectancy in R; it changes the "
                       "drawdown and ruin distribution. Test with the Monte Carlo engine "
                       "rather than the backtester."),
        "component": "RiskSpec.risk_pct",
    },
    {
        "src": SRC_CWT_NOTES, "category": "TRADE_MANAGEMENT", "concept": "sell into strength",
        "observation": ("Interview notes report a 'golden rule' of always selling some into "
                        "strength: one-third to one-half of the position 3-5 days after "
                        "entry, then moving the stop to breakeven. One account of the same "
                        "material puts the fraction at 20-25% and notes it varied a lot."),
        "rule_text": "Scale out 20-50% 3-5 days after entry; move the stop to breakeven.",
        "explicit": True,
        "quant": [
            {"label": "33% after 3 days, then breakeven", "detector": "management",
             "params": {"partials": [{"at_r": 0.0, "fraction": 0.33, "after_days": 3}],
                        "breakeven_after_days": 3}},
            {"label": "50% after 5 days", "detector": "management",
             "params": {"partials": [{"at_r": 0.0, "fraction": 0.5, "after_days": 5}]}},
            {"label": "25% after 4 days", "detector": "management",
             "params": {"partials": [{"at_r": 0.0, "fraction": 0.25, "after_days": 4}]}},
        ],
        "hypothesis": ("Time-based scaling reduces variance. Whether it raises or lowers "
                       "total expectancy is the actual question, and it is testable."),
        "component": "ManagementSpec.partials",
    },
    {
        "src": SRC_CWT_NOTES, "category": "TRADE_MANAGEMENT", "concept": "moving-average trail",
        "observation": ("Interview notes report a golden rule that 'you can't outsmart the "
                        "10- and 20-day moving averages', with the remaining position "
                        "trailed on the 10- or 20-day and exited on a close below it."),
        "rule_text": "Trail the remainder with the 10 or 20 SMA; exit on a close below.",
        "explicit": True,
        "quant": [
            {"label": "trail the 10 SMA on a closing basis", "detector": "trail",
             "params": {"type": "sma", "length": 10, "basis": "close"}},
            {"label": "trail the 20 SMA on a closing basis", "detector": "trail",
             "params": {"type": "sma", "length": 20, "basis": "close"}},
            {"label": "trail the 10 SMA on a low basis", "detector": "trail",
             "params": {"type": "sma", "length": 10, "basis": "low"}},
            {"label": "2x ATR chandelier trail", "detector": "trail",
             "params": {"type": "chandelier", "multiple": 2.0}},
        ],
        "hypothesis": ("10 SMA vs 20 SMA trailing: which produces better risk-adjusted "
                       "returns? The sources do not settle it, so both are kept as separate "
                       "strategy versions and compared."),
        "component": "TrailSpec.length",
    },
    {
        "src": SRC_CWT_NOTES, "category": "MARKET_REGIME", "concept": "tradeable market",
        "observation": ("Interview notes report that breakouts are traded in uptrending or "
                        "sideways markets, and downtrends are avoided."),
        "rule_text": "Take breakouts in uptrends and sideways markets; stand aside in downtrends.",
        "explicit": True,
        "quant": [
            {"label": "regime in {BULL, BULL_PULLBACK, SIDEWAYS}", "detector": "market_filter",
             "params": {"allowed_regimes": ["BULL", "BULL_PULLBACK", "SIDEWAYS"]}},
            {"label": "regime = BULL only", "detector": "market_filter",
             "params": {"allowed_regimes": ["BULL"]}},
        ],
        "hypothesis": ("Note this is WEAKER than the SAR 10>20 MA rule: 'sideways is fine' "
                       "and 'index 10 MA above 20 MA' disagree in chop. Both are testable."),
        "component": "MarketFilter / RegimeClassifier",
    },
    {
        "src": SRC_QULLAMAGGIE_EP, "category": "SETUP", "concept": "episodic pivot",
        "observation": ("Reported: episodic pivots are driven by a major catalyst -- an "
                        "earnings surprise or news event -- that forces the market to reprice "
                        "the stock rapidly; the concept traces to Pradeep Bonde / Stockbee."),
        "rule_text": "Buy the continuation of a catalyst-driven gap from a quiet base.",
        "explicit": True,
        "quant": [
            {"label": "gap >5% on >4x relative volume from a quiet base",
             "detector": "breakout", "params": {"buffer_pct": 5.0, "min_rvol": 4.0}},
            {"label": "gap >10% on >6x relative volume", "detector": "breakout",
             "params": {"buffer_pct": 10.0, "min_rvol": 6.0}},
        ],
        "hypothesis": ("BLOCKED: the catalyst leg cannot be identified without a "
                       "point-in-time news/earnings feed. Any statistics computed from the "
                       "price signature alone describe 'large gap-ups', a strict superset of "
                       "the real setup, and must be labelled that way."),
        "component": "strategy.library.episodic_pivot_v0_1",
    },
    {
        "src": SRC_KK_STUDY_SITE, "category": "STOCK_SELECTION", "concept": "setup taxonomy",
        "observation": ("A third-party study guide describes the three publicly discussed "
                        "setups as breakout, episodic pivot and parabolic short."),
        "rule_text": "Three distinct setups exist; this build implements the first and a "
                     "partial version of the second.",
        "explicit": False,
        "quant": [],
        "hypothesis": ("The parabolic short is NOT implemented. Shorting has different "
                       "borrow, gap and assignment risk and would need its own execution "
                       "model; implementing it as a sign flip of the long engine would be "
                       "wrong."),
        "component": "strategy.library",
    },
]
