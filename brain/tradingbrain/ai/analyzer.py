"""The "is this a good trade?" engine, and the anti-bias layer.

Structure of the answer is fixed and always includes the sections the operator
asked for -- including BEAR CASE and INVALIDATION, which are generated from the
same measurements as the bull case rather than being decorative.

The verdict is derived mechanically from the evidence, so it cannot be talked
into agreeing. A setup that fails the strategy definition gets NO_TRADE no
matter how attractive the chart looks.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

import numpy as np

from ..data.panel import Panel, build_panel
from ..data.providers.registry import HUB, DataHub
from ..data.quality import validate
from ..indicators.structure import extension_metrics
from ..provenance import Claim, DataOrigin, EvidenceClass
from ..regime.classifier import classify_market
from ..risk.sizing import position_size, risk_reward
from ..sectors.ranking import SectorRanker, rank_sectors, rank_themes
from ..stats.analogs import AnalogQuery, analog_statistics, find_historical_setups
from ..strategy.evaluator import Evaluator, SymbolContext
from ..strategy.library import get_strategy
from ..strategy.spec import StrategySpec


@dataclasses.dataclass
class TradeIdea:
    symbol: str
    direction: str = "long"
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    position_size: int | None = None
    account_equity: float = 100_000.0
    risk_pct: float = 1.0
    timeframe: str = "daily swing"
    option: dict[str, Any] | None = None
    as_of: dt.date | None = None
    strategy_key: str = "sar_v1_1_adr_stop"
    note: str = ""


def analyze_trade(idea: TradeIdea, hub: DataHub = HUB, panel: Panel | None = None,
                  spec: StrategySpec | None = None,
                  with_analogs: bool = True) -> dict[str, Any]:
    spec = spec or get_strategy(idea.strategy_key)
    sym = idea.symbol.upper()
    out: dict[str, Any] = {"symbol": sym, "strategy": f"{spec.name} v{spec.version}"}

    try:
        ser = hub.daily(sym, end=idea.as_of)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "symbol": sym,
                "reason": f"no price data available for {sym}: {exc}"}
    if len(ser) < 80:
        return {"ok": False, "symbol": sym,
                "reason": f"only {len(ser)} bars of history for {sym}; not enough to analyse"}

    panel = panel if panel is not None else build_panel(hub=hub)
    i = len(ser) - 1
    as_of = ser.ts[i].date()
    close = float(ser.close[i])
    ctx = SymbolContext(ser, spec)
    ev = Evaluator(spec)

    # ---------------- MARKET ----------------------------------------------
    regime = classify_market(as_of, hub, with_breadth=True)

    # ---------------- SECTOR / THEME --------------------------------------
    sect = rank_sectors(as_of, hub, panel)
    them = rank_themes(as_of, hub, panel)
    sector_name = hub.sector_map().get(sym)
    sector_row = next((g for g in sect["ranks"] if g["name"] == sector_name), None)
    n_sect = len(sect["ranks"]) or 1
    sector_pct = (100.0 * (n_sect - sector_row["rank"]) / max(n_sect - 1, 1)
                  if sector_row else None)
    my_themes = [t for t, mem in hub.themes().items() if sym in mem]
    theme_rows = [g for g in them["ranks"] if g["name"] in my_themes]

    # ---------------- STOCK -----------------------------------------------
    ranker = SectorRanker(hub, panel)
    prow = panel.row(as_of)
    rs = None
    if sym in panel.symbols and prow >= 0:
        v = ranker._rs_percentile(spec.setup.rs_lookback)[prow, panel.col(sym)]
        rs = float(v) if np.isfinite(v) else None

    # ---------------- SETUP -----------------------------------------------
    triggered = bool(ctx.breakouts[i])
    verdict = ev.evaluate(ctx, i, require_breakout=triggered, rs_percentile=rs,
                          sector_percentile=sector_pct, collect_all_reasons=True)
    base = ctx.cons.at(i - 1 if triggered else i)
    ext = extension_metrics(ser.close, ser.high, ser.low, i)

    out.update({
        "ok": True,
        "as_of": as_of.isoformat(),
        "close": round(close, 4),
        "data_origin": ser.origin.value,
        "data_provider": ser.provider,
        "data_quality": validate(ser).to_dict(),
        "setup": {
            "state": ("breakout triggered on this bar" if triggered else
                      "no breakout on this bar -- evaluated as a pre-breakout candidate, "
                      "so 'matches strategy' means every rule EXCEPT the trigger is met"),
            "matches_strategy": verdict.qualifies,
            "failed_checks": verdict.reasons_failed,
            "measurements": verdict.snapshot,
            "base": base.to_dict() if base else None,
        },
        "market": {
            "regime": regime.label, "volatility": regime.volatility_label,
            "strength": round(regime.strength, 1) if np.isfinite(regime.strength) else None,
            "index_filter_passes": regime.filter_pass,
            "index_filter_detail": regime.filter_detail,
            "breadth": regime.breadth,
        },
        "sector": {
            "name": sector_name,
            "rank": sector_row["rank"] if sector_row else None,
            "of": n_sect,
            "percentile": round(sector_pct, 1) if sector_pct is not None else None,
            "components": sector_row["components"] if sector_row else None,
            "leaders": sector_row["leaders"] if sector_row else None,
        },
        "themes": [{"name": g["name"], "rank": g["rank"], "score": g["score"]}
                   for g in theme_rows],
        "stock": {
            "relative_strength_percentile": round(rs, 1) if rs is not None else None,
            "return_1m": _ret(ser, 21), "return_3m": _ret(ser, 63),
            "return_6m": _ret(ser, 126), "return_12m": _ret(ser, 252),
            "adr_pct": round(float(ctx.adr[i]), 2) if np.isfinite(ctx.adr[i]) else None,
            "rvol": round(float(ctx.rvol[i]), 2) if np.isfinite(ctx.rvol[i]) else None,
            "dollar_volume_20d": round(float(ctx.dollar_vol[i]), 0)
            if np.isfinite(ctx.dollar_vol[i]) else None,
            "extension": {k: (round(v, 2) if np.isfinite(v) else None) for k, v in ext.items()},
            "moving_averages": {str(n): (round(float(ctx.ma(n)[i]), 4)
                                         if np.isfinite(ctx.ma(n)[i]) else None)
                                for n in (10, 20, 50, 200)},
            "orderliness_r2_20": round(float(ctx.r2[i]), 3) if np.isfinite(ctx.r2[i]) else None,
        },
    })

    # ---------------- RISK / REWARD ---------------------------------------
    entry = idea.entry if idea.entry is not None else close
    stop = idea.stop if idea.stop is not None else (
        float(ser.low[i]) if triggered else (float(base.low) if base else None))
    plan = None
    if stop is not None:
        plan = position_size(idea.account_equity, idea.risk_pct, entry, stop,
                             idea.target, idea.direction,
                             adr_pct=float(ctx.adr[i]) if np.isfinite(ctx.adr[i]) else None,
                             max_stop_adr_multiple=spec.stop.max_stop_adr_multiple)
    out["risk"] = {
        "entry": round(entry, 4),
        "stop": round(stop, 4) if stop is not None else None,
        "target": idea.target,
        "plan": plan.to_dict() if plan else None,
        "risk_reward": (risk_reward(entry, stop, idea.target, idea.direction)
                        if stop is not None and idea.target else None),
        "guards": __import__("tradingbrain.risk.guards", fromlist=["GUARDS"]).GUARDS.status(),
    }

    # ---------------- HISTORICAL ANALOGS ----------------------------------
    analogs: dict[str, Any] = {"run": False,
                               "reason": "skipped (with_analogs=False)"}
    if with_analogs:
        q = AnalogQuery(spec=spec, start=None, end=as_of, target_r=3.0,
                        exclude_symbol=sym, max_events=6000)
        events, meta = find_historical_setups(q, hub, panel)
        analogs = analog_statistics(events, q, meta)
        analogs["run"] = True
        analogs["note"] = (f"Computed on every other symbol in the universe ({sym} itself is "
                           "excluded so the analog set is not contaminated by the very name "
                           "being analysed).")
    out["historical_analogs"] = analogs

    # ---------------- BULL / BEAR / INVALIDATION --------------------------
    out["bull_case"] = _bull_case(out)
    out["bear_case"] = _bear_case(out, ctx, i, spec)
    out["invalidation"] = _invalidation(out, stop, base)
    out["verdict"] = _verdict(out)
    out["evidence_quality"] = _evidence_quality(out)
    out["claim"] = Claim(
        statement=f"{sym} assessed as {out['verdict']['label']}: {out['verdict']['headline']}",
        evidence=EvidenceClass.INFERENCE,
        value=out["verdict"]["label"],
        period=as_of.isoformat(),
        data_source=ser.provider,
        data_origin=DataOrigin(ser.origin.value),
        methodology=("Mechanical scoring from the strategy definition, market regime, sector "
                     "rank, relative strength and historical analog statistics. The verdict is "
                     "computed, not argued -- see verdict.reasons for the exact inputs."),
        caveats=["A verdict describes how well the situation matches a definition that has "
                 "itself not been proven profitable. It is not advice and not a forecast."],
    ).to_dict()
    return out


def _ret(ser, bars: int) -> float | None:
    if len(ser) <= bars:
        return None
    return round(float((ser.close[-1] / ser.close[-1 - bars] - 1) * 100), 2)


def _bull_case(a: dict[str, Any]) -> list[str]:
    pts: list[str] = []
    s = a["setup"]["measurements"]
    if a["setup"]["matches_strategy"]:
        pts.append("The bar satisfies every rule in the active strategy definition.")
    if s.get("prior_move_pct"):
        pts.append(f"Prior advance of {s['prior_move_pct']}% over {s.get('prior_move_bars')} "
                   "bars supplies the momentum the setup requires.")
    if s.get("base_quality") is not None:
        pts.append(f"Base quality {s['base_quality']} (depth {s.get('base_depth_pct')}%, "
                   f"range contraction {s.get('base_range_contraction')}, volume "
                   f"contraction {s.get('base_volume_contraction')}).")
    if a["market"]["index_filter_passes"]:
        pts.append(f"Market regime is {a['market']['regime']} and the index moving-average "
                   "filter passes.")
    if a["sector"]["percentile"] is not None and a["sector"]["percentile"] >= 60:
        pts.append(f"Sector {a['sector']['name']} ranks {a['sector']['rank']} of "
                   f"{a['sector']['of']} ({a['sector']['percentile']:.0f}th percentile).")
    rs = a["stock"]["relative_strength_percentile"]
    if rs is not None and rs >= 70:
        pts.append(f"Relative strength in the {rs:.0f}th percentile of the scanned universe.")
    h = (a.get("historical_analogs") or {}).get("horizons", {})
    if "10" in h and h["10"]["n"] >= 30:
        pts.append(h["10"]["statement"])
    return pts or ["No affirmative evidence was found for this trade."]


def _bear_case(a: dict[str, Any], ctx: SymbolContext, i: int,
               spec: StrategySpec) -> list[str]:
    """Always populated. This section exists to argue against the trade."""
    pts: list[str] = []
    for r in a["setup"]["failed_checks"]:
        pts.append(f"Rule failure: {r}")
    if not a["setup"]["matches_strategy"] and not a["setup"]["failed_checks"]:
        pts.append("The bar does not match the strategy definition.")
    if not a["market"]["index_filter_passes"]:
        pts.append("The index moving-average filter FAILS. The source material's own rule "
                   "is not to take breakouts in this condition; whether that rule helps is "
                   "testable in the Research Lab.")
    if a["market"]["regime"] in ("BEAR", "CORRECTION"):
        pts.append(f"Market regime is {a['market']['regime']}: momentum breakouts have a "
                   "materially different character in this environment.")
    if a["market"]["volatility"] == "HIGH":
        pts.append("Index volatility is in its high regime; stops get hit by noise more often.")
    ext = a["stock"]["extension"]
    if ext.get("pct_above_ma") is not None and ext["pct_above_ma"] > 15:
        pts.append(f"Price is {ext['pct_above_ma']:.1f}% above the 20 SMA "
                   f"({ext.get('adr_above_ma')} ADRs): buying here is buying extension.")
    if ext.get("pct_from_20d_high") is not None and ext["pct_from_20d_high"] > -0.5:
        pts.append("Price is at the top of its 20-day range; a failed breakout has the "
                   "furthest to fall back to support.")
    sp = a["sector"]["percentile"]
    if sp is not None and sp < 40:
        pts.append(f"Sector {a['sector']['name']} is in the bottom {sp:.0f}th percentile; "
                   "the setup is fighting its own group.")
    rs = a["stock"]["relative_strength_percentile"]
    if rs is not None and rs < 50:
        pts.append(f"Relative strength is only the {rs:.0f}th percentile -- this is not a "
                   "leading stock by the measure the strategy uses.")
    plan = (a.get("risk") or {}).get("plan") or {}
    for w in plan.get("warnings", []):
        pts.append(w)
    rr = (a.get("risk") or {}).get("risk_reward")
    if rr and rr.get("reward_risk") is not None and rr["reward_risk"] < 2:
        pts.append(f"Reward/risk is only {rr['reward_risk']:.2f}; at that ratio the setup "
                   f"needs a {rr.get('breakeven_win_rate_pct', 0):.0f}% win rate just to "
                   "break even before costs.")
    an = a.get("historical_analogs") or {}
    if an.get("run"):
        n = an.get("n", 0)
        if n < 30:
            pts.append(f"Only {n} historical analogs were found. That is too few to support "
                       "any statistical claim about this setup.")
        else:
            h = an.get("horizons", {}).get("10")
            if h and h["pct_positive"] < 55:
                pts.append(f"Historically only {h['pct_positive']}% of {h['n']} analogs were "
                           "positive after 10 days -- the base rate is close to a coin flip.")
            path = an.get("path") or {}
            if path.get("pct_hit_stop_first") and path["pct_hit_stop_first"] > 50:
                pts.append(f"{path['pct_hit_stop_first']}% of resolved analogs hit the stop "
                           f"before reaching +{path.get('target_r')}R.")
    if a["data_origin"] == "SYNTHETIC":
        pts.append("DATA IS SYNTHETIC. Every statistic above describes a generated market, "
                   "not a real one, and cannot support a real trading decision.")
    dq = a.get("data_quality") or {}
    for issue in dq.get("issues", []):
        if issue["severity"] == "error":
            pts.append(f"Data quality error ({issue['code']}): {issue['detail']}")
    return pts or ["No specific objection was found. That is not the same as no risk: the "
                   "checks run here are the ones this system knows how to run."]


def _invalidation(a: dict[str, Any], stop: float | None,
                  base: Any) -> list[str]:
    out = []
    if stop is not None:
        out.append(f"A trade through {stop:.2f} means the low of the signal bar failed; the "
                   "premise (buyers defend the breakout) is falsified.")
    if base is not None:
        out.append(f"A close back inside the base (below {base.high:.2f}) means the breakout "
                   "did not hold -- historically the most common failure of this setup.")
    ma = a["stock"]["moving_averages"]
    if ma.get("20"):
        out.append(f"A close below the 20 SMA ({ma['20']}) ends the 'riding the rising "
                   "averages' condition the setup depends on.")
    if a["market"]["index_filter_passes"]:
        out.append("The index 10 SMA crossing back below the 20 SMA removes the market "
                   "condition under which this setup was taken.")
    out.append("The sector dropping out of the top half of the ranking removes the group "
               "support component of the thesis.")
    return out


def _verdict(a: dict[str, Any]) -> dict[str, Any]:
    """Mechanical. The inputs are listed so the conclusion can be checked."""
    reasons: list[str] = []
    score = 0.0

    if a["setup"]["matches_strategy"]:
        score += 35
        reasons.append("+35 matches the strategy definition")
    else:
        reasons.append("+0 does not match the strategy definition")

    if a["market"]["index_filter_passes"]:
        score += 15
        reasons.append("+15 index moving-average filter passes")
    else:
        reasons.append("+0 index moving-average filter fails")

    sp = a["sector"]["percentile"]
    if sp is not None:
        add = 10 * sp / 100.0
        score += add
        reasons.append(f"+{add:.1f} sector percentile {sp:.0f}")
    rs = a["stock"]["relative_strength_percentile"]
    if rs is not None:
        add = 15 * rs / 100.0
        score += add
        reasons.append(f"+{add:.1f} relative strength percentile {rs:.0f}")

    q = a["setup"]["measurements"].get("base_quality")
    if q is not None:
        add = 10 * float(q)
        score += add
        reasons.append(f"+{add:.1f} base quality {q}")

    an = a.get("historical_analogs") or {}
    n = an.get("n", 0)
    h = (an.get("horizons") or {}).get("10")
    if an.get("run") and n >= 30 and h:
        edge = (h["pct_positive"] - 50.0) / 50.0
        add = float(np.clip(15 * edge, -15, 15))
        score += add
        reasons.append(f"{add:+.1f} historical base rate {h['pct_positive']}% positive at "
                       f"10 days (N={h['n']})")
    else:
        reasons.append(f"+0 historical evidence insufficient (N={n})")

    penalties: list[str] = []
    ext = a["stock"]["extension"].get("pct_above_ma")
    if ext is not None and ext > 20:
        score -= 10
        penalties.append(f"-10 extended {ext:.1f}% above the 20 SMA")
    rr = (a.get("risk") or {}).get("risk_reward")
    if rr and rr.get("reward_risk") is not None and rr["reward_risk"] < 1.5:
        score -= 10
        penalties.append(f"-10 reward/risk only {rr['reward_risk']:.2f}")
    plan = (a.get("risk") or {}).get("plan") or {}
    if plan.get("warnings"):
        score -= 5
        penalties.append("-5 risk-plan warning present")
    reasons += penalties

    score = float(np.clip(score, 0, 100))
    if not a["setup"]["matches_strategy"]:
        label = "NO TRADE"
        headline = ("the setup does not meet the strategy's own definition, so there is no "
                    "basis in this framework for taking it")
    elif not a["market"]["index_filter_passes"]:
        label = "LOW-QUALITY SETUP"
        headline = ("the setup qualifies but the market filter the strategy specifies is "
                    "failing")
    elif score >= 72:
        label, headline = "HIGH-QUALITY SETUP", "every component of the definition lines up"
    elif score >= 55:
        label, headline = "MODERATE-QUALITY SETUP", "the setup qualifies with mixed support"
    else:
        label, headline = "LOW-QUALITY SETUP", "the setup qualifies but little else supports it"

    if a["data_origin"] == "SYNTHETIC":
        headline += " -- ON SYNTHETIC DATA, so this verdict is a test of the machinery, "\
                    "not a market judgement"
    return {"label": label, "score": round(score, 1), "headline": headline,
            "reasons": reasons}


def _evidence_quality(a: dict[str, Any]) -> dict[str, Any]:
    an = a.get("historical_analogs") or {}
    n = an.get("n", 0)
    bits = []
    if a["data_origin"] == "SYNTHETIC":
        bits.append("Data is synthetic: evidential value for real trading is ZERO.")
    if n == 0:
        bits.append("No historical analogs.")
    elif n < 30:
        bits.append(f"Only {n} analogs: descriptive at best.")
    elif n < 200:
        bits.append(f"{n} analogs: moderate.")
    else:
        bits.append(f"{n} analogs: adequate sample, though overlapping and correlated.")
    bits.append("The strategy definition itself has not been validated out of sample in this "
                "analysis; run the Research Lab's walk-forward for that.")
    grade = ("NONE" if a["data_origin"] == "SYNTHETIC" else
             "LOW" if n < 30 else "MODERATE" if n < 200 else "REASONABLE")
    return {"grade": grade, "notes": bits}
